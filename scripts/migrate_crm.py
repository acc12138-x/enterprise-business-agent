# -*- coding: utf-8 -*-
"""轻量迁移：为「客户线索 + 转人工」功能补表与列。幂等，可重复执行。

做三件事
  1. 建新表：leads / lead_followups / human_handoffs / conversation_messages
     —— create_all 只创建【不存在】的表，已存在的表不会被改动
  2. 给已存在的 customers 表补 3 个列
     —— create_all **不会**给已有表加列，所以必须手写 ALTER TABLE
  3. 可选：补两个示例销售账号（--seed），让线索自动分配有池子

幂等性：每一步都先探测是否已存在，重复执行无副作用。

用法
    python scripts/migrate_crm.py            # 只迁移
    python scripts/migrate_crm.py --seed     # 迁移 + 补示例销售

⚠️ 对线上库执行前请先备份：
    cp data/app.db data/app.db.bak-$(date +%Y%m%d-%H%M%S)
"""
import os
import sys
from pathlib import Path

# 项目根从脚本自身位置推导，不要写死本机绝对路径
ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from sqlalchemy import inspect, select  # noqa: E402

from app.db.session import get_engine, init_db, session_scope  # noqa: E402

# 需要补列的表：(表名, 列名, 列定义)
ADD_COLUMNS = [
    ("customers", "owner_id", "INTEGER"),
    ("customers", "owner_name", "VARCHAR(64) DEFAULT ''"),
    ("customers", "lead_id", "VARCHAR(32)"),
]

NEW_TABLES = ["leads", "lead_followups", "human_handoffs", "conversation_messages"]


def _columns_of(table: str) -> set:
    """取表的现有列（方言无关，SQLite / MySQL 都能用）。"""
    insp = inspect(get_engine())
    if not insp.has_table(table):
        return set()
    return {c["name"] for c in insp.get_columns(table)}


def step1_create_tables() -> None:
    print("[1/3] 创建新表")
    insp = inspect(get_engine())
    before = {t for t in NEW_TABLES if insp.has_table(t)}
    if before:
        print(f"      已存在，跳过：{', '.join(sorted(before))}")
    init_db()
    insp = inspect(get_engine())
    for t in NEW_TABLES:
        state = "已存在" if t in before else "已创建"
        ok = insp.has_table(t)
        print(f"      {'[OK]' if ok else '[!!]'} {t:<22} {state if ok else '创建失败'}")


def step2_add_columns() -> None:
    print("[2/3] 补列（create_all 不会给已有表加列，需手动 ALTER）")
    engine = get_engine()
    for table, col, ddl in ADD_COLUMNS:
        cols = _columns_of(table)
        if not cols:
            print(f"      [SKIP] 表 {table} 不存在")
            continue
        if col in cols:
            print(f"      [SKIP] {table}.{col} 已存在")
            continue
        sql = f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"
        try:
            with engine.begin() as conn:
                from sqlalchemy import text
                conn.execute(text(sql))
            print(f"      [ADD]  {table}.{col}  <- {ddl}")
        except Exception as e:
            print(f"      [FAIL] {table}.{col}: {type(e).__name__}: {e}")


def step3_seed_sales(do_seed: bool) -> None:
    print("[3/3] 示例销售账号")
    if not do_seed:
        print("      未指定 --seed，跳过")
        return

    from app.db.models.user import User

    seeds = [
        ("U9100", "小林", "13800138002", "华南区"),
        ("U9101", "小陈", "13800138003", "华东区"),
    ]
    with session_scope() as s:
        for uid, name, phone, region in seeds:
            exists = s.execute(select(User).where(User.name == name)).scalar_one_or_none()
            if exists:
                print(f"      [SKIP] {name} 已存在")
                continue
            s.add(User(
                user_id=uid,
                name=name,
                role="agent",          # agent 角色自带 lead.edit，即销售池成员
                job="销售",
                region=region,
                status="online",
                phone=phone,
                max_load=999,
            ))
            print(f"      [ADD]  {name}（{region}，销售）")


def main() -> None:
    do_seed = "--seed" in sys.argv
    print("=" * 60)
    print("CRM 迁移：客户线索 + 转人工")
    print("=" * 60)
    step1_create_tables()
    step2_add_columns()
    step3_seed_sales(do_seed)
    print()
    print("[OK] 迁移完成（本脚本幂等，可重复执行）")


if __name__ == "__main__":
    main()
