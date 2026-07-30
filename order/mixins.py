"""
模型混合类，用于消除重复代码
"""

from django.db import models


class ClothCatalogMixin:
    """提供 cloth_catalog_pk 属性的混合类"""
    
    @property
    def cloth_catalog_pk(self):
        """获取对应的布种目录主键"""
        try:
            from .models import ClothCatalog
            # 检查当前模型是否有 cloth_type 字段
            if hasattr(self, 'cloth_type') and self.cloth_type:
                obj = ClothCatalog.objects.filter(cloth_code=self.cloth_type).first()
                return obj.pk if obj else None
            # 对于 InventoryItem，使用 cloth_name 查找
            elif hasattr(self, 'cloth_name') and self.cloth_name:
                obj = ClothCatalog.objects.filter(
                    models.Q(cloth_code=self.cloth_name) | 
                    models.Q(cloth_name__icontains=self.cloth_name)
                ).first()
                return obj.pk if obj else None
        except Exception:
            return None
        return None


class ProgressStageMixin:
    """提供进度阶段相关属性的混合类"""
    
    @property
    def current_stage_name(self):
        """获取当前进度阶段名称"""
        import json
        try:
            # 检查是否有 progress_stages 字段
            if hasattr(self, 'progress_stages') and self.progress_stages:
                stages = json.loads(self.progress_stages) if self.progress_stages else []
                # 检查是否有 progress_current 字段
                if hasattr(self, 'progress_current') and 0 <= self.progress_current < len(stages):
                    return stages[self.progress_current]
        except Exception:
            pass
        return "-"
    
    @property
    def stage_list(self):
        """获取进度阶段列表"""
        import json
        try:
            if hasattr(self, 'progress_stages') and self.progress_stages:
                return json.loads(self.progress_stages) if self.progress_stages else []
        except Exception:
            pass
        return []
    
    @property
    def progress_percent(self):
        """计算进度百分比"""
        import json
        try:
            if hasattr(self, 'progress_stages') and self.progress_stages:
                stages = json.loads(self.progress_stages) if self.progress_stages else []
                total = len(stages)
                if total > 0 and hasattr(self, 'progress_current'):
                    cur = min(self.progress_current, total - 1)
                    return int((cur + 1) / total * 100)
        except Exception:
            pass
        return 0


class AdminRequiredMixin:
    """管理员权限检查混合类"""
    
    def check_admin_permission(self, request):
        """检查用户是否有管理员权限"""
        if request.user.is_authenticated and hasattr(request.user, 'supplier_profile') and not request.user.is_staff:
            from django.contrib import messages
            messages.warning(request, '无权访问管理面板')
            return False
        return True