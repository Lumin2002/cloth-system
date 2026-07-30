from django import template

register = template.Library()


@register.filter
def get_field(form, field_name):
    """动态获取 ModelForm 字段（模板中不能使用 form[field_name]）。"""
    return form[field_name]


def _copy_params(request, exclude=None):
    params = request.GET.copy()
    for key in exclude or ():
        params.pop(key, None)
    return params


@register.simple_tag(takes_context=True)
def pagination_url(context, page_number):
    """保留当前筛选条件，生成指定页码的查询字符串。"""
    params = _copy_params(context['request'], exclude=('page',))
    params['page'] = page_number
    return '?' + params.urlencode()


@register.simple_tag(takes_context=True)
def filter_url(context, **overrides):
    """生成筛选链接（重置页码，可覆盖单个筛选参数）。"""
    params = _copy_params(context['request'], exclude=('page',))
    for key, value in overrides.items():
        if value in (None, ''):
            params.pop(key, None)
        else:
            params[key] = value
    encoded = params.urlencode()
    return '?' + encoded if encoded else '?'
@register.filter
def get_item(d, k):
    return d.get(str(k))
