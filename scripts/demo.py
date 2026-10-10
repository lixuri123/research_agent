"""Durable CLI submission; execute jobs with python -m research_agent.worker."""
import argparse
import json
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from research_agent.persistence.queue import get_repository


def main():
    parser = argparse.ArgumentParser(description='提交科研任务')
    parser.add_argument('--topic', required=True)
    parser.add_argument('--requirements', default='')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--wait', action='store_true', help='等待独立 Worker 完成或进入审批')
    args = parser.parse_args()
    repo = get_repository()
    record = repo.submit(args.topic, args.requirements)
    if args.wait:
        while record['status'] in {'queued','running'}:
            time.sleep(1)
            record = repo.get(record['task_id'])
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2))
        return
    print('task_id:', record['task_id'])
    print('run_id:', record['run_id'])
    print('status:', record['status'])
    print('report_version_id:', record['current_report_version_id'])
    result = record.get('result') or {}
    if result.get('final_report'):
        print(result['final_report']['content'])
    elif result.get('errors') or result.get('error'):
        print(result.get('errors') or result.get('error'))

if __name__ == '__main__':
    main()
