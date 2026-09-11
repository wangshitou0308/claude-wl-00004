"""Command-line entry point: ``python -m tree_ring_xdate``."""

from __future__ import annotations

import argparse
import sys

from .server import make_server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tree_ring_xdate",
        description="离线树轮交叉定年 HTTP API（标准库 http.server + sqlite3）")
    parser.add_argument("--db", default="tree_ring.db",
                        help="SQLite 数据库文件（默认 ./tree_ring.db）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1）")
    parser.add_argument("--port", type=int, default=8000,
                        help="监听端口（默认 8000）")
    parser.add_argument("--quiet", action="store_true",
                        help="不输出访问日志")
    args = parser.parse_args(argv)

    server = make_server(args.host, args.port, args.db,
                         log=(None if args.quiet else print))
    print(f"树轮交叉定年 API 已启动: http://{args.host}:{args.port}/  "
          f"(db={args.db})", file=sys.stderr)
    print(f"接口文档: http://{args.host}:{args.port}/api/help",
          file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n正在关闭...", file=sys.stderr)
    finally:
        server.server_close()
        server.db_conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
