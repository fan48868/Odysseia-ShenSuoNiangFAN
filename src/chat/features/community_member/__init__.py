"""
社区成员档案上传功能模块
提供社区成员档案上传、管理和查询功能
"""

from .services.community_member_service import community_member_service

__all__ = ['community_member_service']