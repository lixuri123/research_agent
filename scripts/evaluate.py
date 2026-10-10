"""评测入口：运行评测集并输出多维度指标。"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_agent.core.config import get_settings
from research_agent.evaluation.runner import run_and_report


def main() -> None:
    parser = argparse.ArgumentParser(description="科研助手评测")
    parser.add_argument(
        "--dataset",
        default=None,
        help="评测集 JSON 路径（默认读配置 eval_dataset_path）",
    )
    args = parser.parse_args()

    path = args.dataset or get_settings().eval_dataset_path
    metrics = run_and_report(path)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
