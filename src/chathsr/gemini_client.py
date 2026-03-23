from __future__ import annotations

from google import genai
from google.genai import types

from chathsr.config import Settings
from chathsr.errors import ChathsrError
from chathsr.models import RetrievedChunk


class GeminiClient:
    def __init__(self, settings: Settings):
        if not settings.gemini_api_key:
            raise ChathsrError("GEMINI_API_KEY is required for indexing and asking.")
        self.settings = settings
        self.client = genai.Client(api_key=settings.gemini_api_key)

    def embed_document_chunks(
        self, chunks: list[str], *, title: str | None = None
    ) -> list[list[float]]:
        if not chunks:
            return []
        embeddings: list[list[float]] = []
        for chunk in chunks:
            response = self.client.models.embed_content(
                model=self.settings.embedding_model,
                contents=[chunk],
                config=types.EmbedContentConfig(
                    task_type="RETRIEVAL_DOCUMENT",
                    title=title,
                    output_dimensionality=self.settings.embedding_dim,
                ),
            )
            values = _extract_embedding_values(response)
            embeddings.append(values)
        return embeddings

    def embed_query(self, question: str) -> list[float]:
        response = self.client.models.embed_content(
            model=self.settings.embedding_model,
            contents=[question],
            config=types.EmbedContentConfig(
                task_type="RETRIEVAL_QUERY",
                output_dimensionality=self.settings.embedding_dim,
            ),
        )
        return _extract_embedding_values(response)

    def generate_answer(
        self,
        *,
        question: str,
        chunks: list[RetrievedChunk],
        use_cheap_model: bool = False,
    ) -> str:
        if not chunks:
            return "관련 근거를 찾지 못했습니다. 현재 수집된 자료만으로는 답할 수 없습니다."
        model_name = (
            self.settings.cheap_generation_model
            if use_cheap_model
            else self.settings.generation_model
        )
        prompt = _build_prompt(question, chunks)
        response = self.client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "너는 ArcaLive 붕괴 스타레일 채널의 정보 글만 근거로 답변하는 RAG 어시스턴트다. "
                    "반드시 제공된 근거만 사용하고, 근거가 부족하면 모른다고 답하라. "
                    "답변은 한국어로 작성하고, 가능한 문장 끝에 [번호] 형식으로 근거를 표시하라."
                ),
                temperature=0.2,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
            ),
        )
        if response.text:
            return response.text.strip()
        if response.prompt_feedback and response.prompt_feedback.block_reason:
            raise ChathsrError(
                f"Gemini blocked the prompt: {response.prompt_feedback.block_reason}"
            )
        raise ChathsrError("Gemini generation response did not contain text.")


def _build_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    lines = [
        "질문:",
        question.strip(),
        "",
        "근거 문서:",
    ]
    for index, chunk in enumerate(chunks, start=1):
        lines.extend(
            [
                f"[{index}] 제목: {chunk.title}",
                f"[{index}] 작성일: {chunk.created_at or '알 수 없음'}",
                f"[{index}] URL: {chunk.url}",
                f"[{index}] 내용:",
                chunk.chunk_text,
                "",
            ]
        )
    lines.extend(
        [
            "답변 규칙:",
            "1. 제공된 근거 밖의 사실은 쓰지 말 것.",
            "2. 근거가 충분하지 않으면 모른다고 명시할 것.",
            "3. 핵심 주장 뒤에는 가능한 경우 [번호]를 붙일 것.",
        ]
    )
    return "\n".join(lines)


def _extract_embedding_values(response: types.EmbedContentResponse) -> list[float]:
    embeddings = response.embeddings or []
    if not embeddings or not embeddings[0].values:
        raise ChathsrError("Gemini embedding response did not contain a vector.")
    return embeddings[0].values
