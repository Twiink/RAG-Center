from pathlib import Path

import fitz

from rag_center.infrastructure.providers.parsers.base import DocumentParser, ParsedDocument


class PdfParser(DocumentParser):
    @property
    def name(self) -> str:
        return "pdf"

    def supports(self, filename: str, mime_type: str | None = None) -> bool:
        return Path(filename).suffix.lower() == ".pdf"

    async def parse(self, file_bytes: bytes, *, filename: str) -> ParsedDocument:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        parts: list[str] = []
        for index in range(len(doc)):
            page = doc[index]
            text = page.get_text("text").strip()
            if text:
                parts.append(f"## 第 {index + 1} 页\n\n{text}")
        return ParsedDocument(
            content="\n\n".join(parts).strip(),
            source_type="pdf",
            metadata={"parser": self.name, "page_count": len(doc)},
        )
