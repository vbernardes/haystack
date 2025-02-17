import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from haystack.nodes.file_converter.base import BaseConverter
from haystack.schema import Document

logger = logging.getLogger(__name__)


class PDFToTextConverter(BaseConverter):
    def __init__(
        self,
        remove_numeric_tables: bool = False,
        valid_languages: Optional[List[str]] = None,
        id_hash_keys: Optional[List[str]] = None,
        encoding: Optional[str] = "UTF-8",
        keep_physical_layout: bool = False,
    ):
        """
        :param remove_numeric_tables: This option uses heuristics to remove numeric rows from the tables.
                                      The tabular structures in documents might be noise for the reader model if it
                                      does not have table parsing capability for finding answers. However, tables
                                      may also have long strings that could possible candidate for searching answers.
                                      The rows containing strings are thus retained in this option.
        :param valid_languages: validate languages from a list of languages specified in the ISO 639-1
                                (https://en.wikipedia.org/wiki/ISO_639-1) format.
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text.
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        :param encoding: Encoding that will be passed as `-enc` parameter to `pdftotext`.
                         Defaults to "UTF-8" in order to support special characters (e.g. German Umlauts, Cyrillic ...).
                         (See list of available encodings, such as "Latin1", by running `pdftotext -listenc` in the terminal)
        :param keep_physical_layout: This option will maintain original physical layout on the extracted text.
            It works by passing the `-layout` parameter to `pdftotext`. When disabled, PDF is read in the stream order.
        """
        super().__init__(
            remove_numeric_tables=remove_numeric_tables, valid_languages=valid_languages, id_hash_keys=id_hash_keys
        )
        try:
            subprocess.run(
                ["pdftotext", "-v"], shell=False, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                """pdftotext is not installed. It is part of xpdf or poppler-utils software suite.

                   Installation on Linux:
                   wget --no-check-certificate https://dl.xpdfreader.com/xpdf-tools-linux-4.04.tar.gz &&
                   tar -xvf xpdf-tools-linux-4.04.tar.gz && sudo cp xpdf-tools-linux-4.04/bin64/pdftotext /usr/local/bin

                   Installation on MacOS:
                   brew install xpdf

                   You can find more details here: https://www.xpdfreader.com
                """
            )

        self.encoding = encoding
        self.keep_physical_layout = keep_physical_layout

    def convert(
        self,
        file_path: Path,
        meta: Optional[Dict[str, Any]] = None,
        remove_numeric_tables: Optional[bool] = None,
        valid_languages: Optional[List[str]] = None,
        encoding: Optional[str] = None,
        id_hash_keys: Optional[List[str]] = None,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
    ) -> List[Document]:
        """
        Extract text from a .pdf file using the pdftotext library (https://www.xpdfreader.com/pdftotext-man.html)

        :param file_path: Path to the .pdf file you want to convert
        :param meta: Optional dictionary with metadata that shall be attached to all resulting documents.
                     Can be any custom keys and values.
        :param remove_numeric_tables: This option uses heuristics to remove numeric rows from the tables.
                                      The tabular structures in documents might be noise for the reader model if it
                                      does not have table parsing capability for finding answers. However, tables
                                      may also have long strings that could possible candidate for searching answers.
                                      The rows containing strings are thus retained in this option.
        :param valid_languages: validate languages from a list of languages specified in the ISO 639-1
                                (https://en.wikipedia.org/wiki/ISO_639-1) format.
                                This option can be used to add test for encoding errors. If the extracted text is
                                not one of the valid languages, then it might likely be encoding error resulting
                                in garbled text.
        :param encoding: Encoding that overwrites self.encoding and will be passed as `-enc` parameter to `pdftotext`.
                         (See list of available encodings by running `pdftotext -listenc` in the terminal)
        :param keep_physical_layout: This option will maintain original physical layout on the extracted text.
            It works by passing the `-layout` parameter to `pdftotext`. When disabled, PDF is read in the stream order.
        :param id_hash_keys: Generate the document id from a custom list of strings that refer to the document's
            attributes. If you want to ensure you don't have duplicate documents in your DocumentStore but texts are
            not unique, you can modify the metadata and pass e.g. `"meta"` to this field (e.g. [`"content"`, `"meta"`]).
            In this case the id will be generated by using the content and the defined metadata.
        :param start_page: The page number where to start the conversion
        :param end_page: The page number where to end the conversion.
        """
        if remove_numeric_tables is None:
            remove_numeric_tables = self.remove_numeric_tables
        if valid_languages is None:
            valid_languages = self.valid_languages
        if id_hash_keys is None:
            id_hash_keys = self.id_hash_keys

        keep_physical_layout = self.keep_physical_layout

        pages = self._read_pdf(
            file_path, layout=keep_physical_layout, encoding=encoding, start_page=start_page, end_page=end_page
        )

        cleaned_pages = []
        for page in pages:
            # pdftotext tool provides an option to retain the original physical layout of a PDF page. This behaviour
            # can be toggled by using the layout param.
            #  layout=True
            #      + table structures get retained better
            #      - multi-column pages(eg, research papers) gets extracted with text from multiple columns on same line
            #  layout=False
            #      + keeps strings in content stream order, hence multi column layout works well
            #      - cells of tables gets split across line
            #
            #  Here, as a "safe" default, layout is turned off.
            lines = page.splitlines()
            cleaned_lines = []
            for line in lines:
                words = line.split()
                digits = [word for word in words if any(i.isdigit() for i in word)]

                # remove lines having > 40% of words as digits AND not ending with a period(.)
                if remove_numeric_tables:
                    if words and len(digits) / len(words) > 0.4 and not line.strip().endswith("."):
                        logger.debug("Removing line '%s' from %s", line, file_path)
                        continue
                cleaned_lines.append(line)

            page = "\n".join(cleaned_lines)
            cleaned_pages.append(page)

        if valid_languages:
            document_text = "".join(cleaned_pages)
            if not self.validate_language(document_text, valid_languages):
                logger.warning(
                    "The language for %s is not one of %s. The file may not have "
                    "been decoded in the correct text format.",
                    file_path,
                    valid_languages,
                )

        text = "\f".join(cleaned_pages)
        document = Document(content=text, meta=meta, id_hash_keys=id_hash_keys)
        return [document]

    def _read_pdf(
        self,
        file_path: Path,
        layout: bool,
        encoding: Optional[str] = None,
        start_page: Optional[int] = None,
        end_page: Optional[int] = None,
    ) -> List[str]:
        """
        Extract pages from the pdf file at file_path.

        :param file_path: path of the pdf file
        :param layout: whether to retain the original physical layout for a page. If disabled, PDF pages are read in
                       the content stream order.
        :param encoding: Encoding that overwrites self.encoding and will be passed as `-enc` parameter to `pdftotext`.
                         (See list of available encodings by running `pdftotext -listenc` in the terminal)
        :param start_page: The page number where to start the conversion
        :param end_page: The page number where to end the conversion.
        """
        if not encoding:
            encoding = self.encoding

        start_page = start_page or 1

        command = ["pdftotext", "-enc", str(encoding), "-layout" if layout else "-raw", "-f", str(start_page)]

        if end_page is not None:
            command.extend(["-l", str(end_page)])

        command.extend([str(file_path), "-"])

        output = subprocess.run(command, stdout=subprocess.PIPE, shell=False, check=False)
        document = output.stdout.decode(errors="ignore")
        document = "\f" * (start_page - 1) + document  # tracking skipped pages for correct page numbering
        pages = document.split("\f")
        pages = pages[:-1]  # the last page in the split is always empty.

        return pages
