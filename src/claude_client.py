import anthropic
from .prompts import SYSTEM_PROMPT, SQL_GENERATION_PROMPT, ANALYSIS_PROMPT, IMPORT_SUMMARY_PROMPT

_client = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _system_with_cache() -> list[dict]:
    return [
        {
            "type": "text",
            "text": SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def generate_sql(question: str, schema: str) -> str:
    client = _get_client()
    prompt = SQL_GENERATION_PROMPT.format(schema=schema, question=question)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        output_config={"effort": "medium"},
        system=_system_with_cache(),
        messages=[{"role": "user", "content": prompt}],
    )
    sql = response.content[0].text.strip()
    # Strip any accidental markdown code fences
    if sql.startswith("```"):
        lines = sql.split("\n")
        sql = "\n".join(lines[1:-1]) if lines[-1].strip() == "```" else "\n".join(lines[1:])
    return sql


def analyze_results(question: str, results_table: str) -> str:
    client = _get_client()
    prompt = ANALYSIS_PROMPT.format(results_table=results_table, question=question)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        system=_system_with_cache(),
        messages=[{"role": "user", "content": prompt}],
    )
    # Adaptive thinking may prepend a thinking block; grab the last text block
    for block in reversed(response.content):
        if block.type == "text":
            return block.text
    return ""


def summarize_import(summary: str) -> str:
    client = _get_client()
    prompt = IMPORT_SUMMARY_PROMPT.format(summary=summary)

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=512,
        output_config={"effort": "low"},
        system=_system_with_cache(),
        messages=[{"role": "user", "content": prompt}],
    )
    return response.content[0].text.strip()
