"""CLI 入口：python -m research_agent.main --topic "主题"
"""
import argparse
import logging
import sys


def main():
    parser = argparse.ArgumentParser(description="AI 自动调研报告生成器")
    parser.add_argument("--topic", "-t", default="研究下 AI 编程助手",
                        help="研究主题")
    parser.add_argument("--auto", action="store_true",
                        help="跳过澄清交互，用默认 Brief 直接跑（测试用）")
    parser.add_argument("--format", "-f", default=None,
                        choices=["pdf", "md", "both"],
                        help="导出格式（默认读取配置，通常为 pdf）")
    parser.add_argument("--queries", type=int, default=6,
                        help="最多检索查询数")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING)

    from .pipeline import run
    run(
        topic=args.topic,
        interactive=not args.auto,
        auto_brief=None if not args.auto else {"topic": args.topic},
        output_format=args.format,
        max_queries=args.queries,
    )


if __name__ == "__main__":
    sys.exit(main())
