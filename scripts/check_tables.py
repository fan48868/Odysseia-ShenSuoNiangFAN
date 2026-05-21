import sqlite3
import os
import json

# 获取项目根目录
_PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CHAT_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "chat.db")
WORLD_BOOK_DB_PATH = os.path.join(_PROJECT_ROOT, "data", "world_book.sqlite3")

print(f"Chat Database path: {CHAT_DB_PATH}")
print(f"World Book Database path: {WORLD_BOOK_DB_PATH}")

# 检查 chat.db
try:
    conn = sqlite3.connect(CHAT_DB_PATH)
    cursor = conn.cursor()
    
    # 查询所有表名
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    print('\nTables in chat.db:')
    for table in tables:
        print(table[0])
        
    # 查询users表的结构
    print('\nColumns in users table:')
    cursor.execute("PRAGMA table_info(users);")
    columns = cursor.fetchall()
    for col in columns:
        print(col)
        
    # 查询users表中的数据
    print('\nData in users table:')
    cursor.execute("SELECT * FROM users LIMIT 5;")
    rows = cursor.fetchall()
    for row in rows:
        print(row)
        
    conn.close()
except Exception as e:
    print(f"Error with chat.db: {e}")

# 检查 world_book.sqlite3
try:
    conn = sqlite3.connect(WORLD_BOOK_DB_PATH)
    cursor = conn.cursor()
    
    # 查询所有表名
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    
    print('\nTables in world_book.sqlite3:')
    for table in tables:
        print(table[0])
        
    # 查询community_members表的结构
    print('\nColumns in community_members table:')
    cursor.execute("PRAGMA table_info(community_members);")
    columns = cursor.fetchall()
    for col in columns:
        print(col)
        
    # 查询community_members表中的数据
    print('\nData in community_members table:')
    cursor.execute("SELECT * FROM community_members LIMIT 5;")
    rows = cursor.fetchall()
    for row in rows:
        print(row)
        # 如果有content_json字段，尝试解析它
        if len(row) > 5 and row[5]:  # content_json是第6个字段（索引5）
            try:
                content = json.loads(row[5])
                print(f"  Parsed content_json: {content}")
            except json.JSONDecodeError:
                print(f"  Failed to parse content_json: {row[5]}")
        
    conn.close()
except Exception as e:
    print(f"Error with world_book.sqlite3: {e}")