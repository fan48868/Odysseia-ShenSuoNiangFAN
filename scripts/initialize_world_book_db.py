import sqlite3
import os

def initialize_database():
    """
    Initializes the SQLite database and creates the necessary tables for the world book feature.
    """
    db_path = os.path.join('data', 'world_book.sqlite3')
    os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Table for Categories
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS categories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE
    )
    ''')

    # Table for Community Members
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS community_members (
        id TEXT PRIMARY KEY,
        title TEXT,
        discord_id TEXT,
        discord_number_id TEXT,
        history TEXT,
        content_json TEXT,
        status TEXT DEFAULT 'approved'
    )
    ''')

    # Table for Member's Discord Nicknames (One-to-Many)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS member_discord_nicknames (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        member_id TEXT NOT NULL,
        nickname TEXT NOT NULL,
        FOREIGN KEY (member_id) REFERENCES community_members(id)
    )
    ''')

    # Table for General Knowledge Entries
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS general_knowledge (
        id TEXT PRIMARY KEY,
        title TEXT,
        name TEXT,
        content_json TEXT,
        category_id INTEGER,
        contributor_id INTEGER,  -- 添加 contributor_id 列
        created_at TEXT,         -- 添加 created_at 列
        status TEXT DEFAULT 'approved',
        FOREIGN KEY (category_id) REFERENCES categories(id)
    )
    ''')

    # Table for Aliases (One-to-Many for general_knowledge)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS aliases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id TEXT NOT NULL,
        alias TEXT NOT NULL,
        FOREIGN KEY (entry_id) REFERENCES general_knowledge(id)
    )
    ''')
    
    # Table for Refers To (One-to-Many for general_knowledge, e.g., for slangs)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS knowledge_refers_to (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id TEXT NOT NULL,
        reference TEXT NOT NULL,
        FOREIGN KEY (entry_id) REFERENCES general_knowledge(id)
    )
    ''')

    # Table for Pending Entries
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS pending_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_type TEXT NOT NULL,
        data_json TEXT NOT NULL,
        message_id INTEGER NOT NULL,
        channel_id INTEGER NOT NULL,
        guild_id INTEGER NOT NULL,
        proposer_id INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL
    )
    ''')

    conn.commit()
    conn.close()
    print(f"Database initialized successfully at '{db_path}'")

if __name__ == '__main__':
    initialize_database()