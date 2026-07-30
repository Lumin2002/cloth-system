"""
自定义装饰器，用于消除视图中的重复代码
"""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect


def admin_required(view_func):
    """
    组合装饰器：@login_required + @require_admin
    要求用户登录且是管理员（不是供应商账号）
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        # 检查是否是供应商账号且不是管理员
        if request.user.is_authenticated and hasattr(request.user, 'supplier_profile') and not request.user.is_staff:
            from django.contrib import messages
            messages.warning(request, '无权访问管理面板')
            return redirect('supplier_dashboard')
        return view_func(request, *args, **kwargs)
    
    # 应用 @login_required 装饰器
    return login_required(_wrapped_view)


def validate_ids(view_func):
    """
    验证ID列表的通用装饰器
    用于批量操作，验证ID格式并返回有效的ID列表
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        item_ids = request.POST.getlist('item_ids') or request.GET.getlist('ids')
        if not item_ids:
            from django.contrib import messages
            messages.warning(request, '请先勾选要操作的项目')
            return redirect(request.POST.get('next') or 'order_list')
        
        try:
            pk_list = [int(pk) for pk in item_ids]
        except ValueError:
            from django.contrib import messages
            messages.error(request, 'ID格式无效')
            return redirect(request.POST.get('next') or 'order_list')
        
        request.valid_ids = pk_list
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view


def validate_file_upload(view_func):
    """
    验证文件上传的通用装饰器
    检查文件类型和内容
    """
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        from django.http import JsonResponse
        import logging
        
        logger = logging.getLogger(__name__)
        upload = request.FILES.get('excel_file')
        
        if not upload:
            return JsonResponse({'error': '请选择要导入的 Excel 文件'}, status=400)
        
        fname = upload.name.lower().strip()
        # 兼容 .xls / .xlsx 大小写
        if not fname.endswith(".xlsx") and not fname.endswith(".xls"):
            return JsonResponse({'error': '仅支持 .xlsx / .xls Excel 文件'}, status=400)
        
        try:
            file_bytes = upload.read()
            logger.info(f"文件读取成功，字节大小：{len(file_bytes)}")
        except Exception as exc:
            logger.error(f"读取文件异常：{exc}")
            return JsonResponse({'error': f'读取文件失败：{exc}'}, status=400)
        
        if not file_bytes:
            logger.warning("文件为空")
            return JsonResponse({'error': '文件为空'}, status=400)
        
        request.upload_file_bytes = file_bytes
        return view_func(request, *args, **kwargs)
    
    return _wrapped_view