"""Track C: LLM layer. Entry points: get_llm_client(), parse_job_description(text, llm),
analyze_skill_gap(job, profile, chunks, llm) (already citation-verified), PROMPT_VERSION."""
from docint.llm.client import get_llm_client  # noqa: F401
from docint.llm.jd_parser import parse_job_description  # noqa: F401
from docint.llm.skillgap import PROMPT_VERSION, analyze_skill_gap  # noqa: F401
