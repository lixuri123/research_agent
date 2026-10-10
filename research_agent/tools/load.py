"""工具聚合加载：确保所有工具模块被导入并注册。"""

from research_agent.tools import paper_search  # noqa: F401
from research_agent.tools import knowledge_base  # noqa: F401
from research_agent.tools import pdf_parse  # noqa: F401
from research_agent.tools import web_search  # noqa: F401
from research_agent.tools import browser  # noqa: F401

__all__ = ["paper_search", "knowledge_base", "pdf_parse", "web_search", "browser"]
