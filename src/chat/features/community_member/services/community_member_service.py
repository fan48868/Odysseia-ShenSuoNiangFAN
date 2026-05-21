import logging
import sqlite3
import os
from typing import Optional, Dict, Any

from src import config
import json

log = logging.getLogger(__name__)

class CommunityMemberService:
    """社区成员档案服务，处理社区成员档案的相关操作"""
    
    def __init__(self):
        self.db_path = os.path.join(config.DATA_DIR, 'world_book.sqlite3')
    
    def _get_connection(self):
        """获取数据库连接"""
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as e:
            log.error(f"连接到世界书数据库失败: {e}", exc_info=True)
            return None
    
    async def get_community_member_by_id(self, member_id: str) -> Optional[Dict[str, Any]]:
        """根据成员ID获取社区成员档案"""
        conn = self._get_connection()
        if not conn:
            return None
            
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM community_members WHERE id = ?",
                (member_id,)
            )
            member_row = cursor.fetchone()
            
            if member_row:
                member_dict = dict(member_row)
                
                # 解析 content_json
                if member_dict.get('content_json'):
                    member_dict['content'] = json.loads(member_dict['content_json'])
                    del member_dict['content_json']
                
                return member_dict
                
        except sqlite3.Error as e:
            log.error(f"获取社区成员档案时发生数据库错误: {e}", exc_info=True)
        except Exception as e:
            log.error(f"获取社区成员档案时发生未知错误: {e}", exc_info=True)
        finally:
            conn.close()
            
        return None
    
    async def get_community_members_by_uploader(self, uploader_id: int) -> list:
        """根据上传者ID获取该用户上传的所有社区成员档案"""
        conn = self._get_connection()
        if not conn:
            return []
            
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM community_members WHERE content_json LIKE ?",
                (f'%"uploaded_by":{uploader_id}%',)
            )
            
            members = []
            for row in cursor.fetchall():
                member_dict = dict(row)
                
                # 解析 content_json
                if member_dict.get('content_json'):
                    member_dict['content'] = json.loads(member_dict['content_json'])
                    del member_dict['content_json']
                
                members.append(member_dict)
                
            return members
            
        except sqlite3.Error as e:
            log.error(f"获取上传者档案列表时发生数据库错误: {e}", exc_info=True)
        except Exception as e:
            log.error(f"获取上传者档案列表时发生未知错误: {e}", exc_info=True)
        finally:
            conn.close()
            
        return []
    
    async def get_all_community_members(self) -> list:
        """获取所有社区成员档案"""
        conn = self._get_connection()
        if not conn:
            return []
            
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM community_members ORDER BY id")
            
            members = []
            for row in cursor.fetchall():
                member_dict = dict(row)
                
                # 解析 content_json
                if member_dict.get('content_json'):
                    member_dict['content'] = json.loads(member_dict['content_json'])
                    del member_dict['content_json']
                
                members.append(member_dict)
                
            return members
            
        except sqlite3.Error as e:
            log.error(f"获取所有社区成员档案时发生数据库错误: {e}", exc_info=True)
        except Exception as e:
            log.error(f"获取所有社区成员档案时发生未知错误: {e}", exc_info=True)
        finally:
            conn.close()
            
        return []

# 单例实例
community_member_service = CommunityMemberService()