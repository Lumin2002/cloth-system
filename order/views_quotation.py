# FabricQuotation lookup API
from order.models import FabricQuotation

@require_GET
def quotation_lookup(request):
    """根据面料编号查询最新报价"""
    article_no = request.GET.get("q", "").strip()
    if not article_no:
        return JsonResponse({"error": "Need q parameter"}, status=400)

    qs = FabricQuotation.objects.filter(article_no__icontains=article_no).order_by("-date_sent")
    if not qs.exists():
        return JsonResponse({"found": False})

    q = qs.first()
    data = {
        "found": True,
        "id": q.id,
        "supplier_name": q.supplier_name,
        "article_no": q.article_no,
        "composition": q.composition,
        "weight": q.weight,
        "cuttable_width": q.cuttable_width,
        "price_200m_text": q.price_200m_text,
        "price_200m": str(q.price_200m) if q.price_200m else None,
        "price_200m_print": str(q.price_200m_print) if q.price_200m_print else None,
        "price_200m_unit": q.price_200m_unit,
        "regular_mcq": q.regular_mcq,
        "price_regular_text": q.price_regular_text,
        "price_regular": str(q.price_regular) if q.price_regular else None,
        "price_regular_print": str(q.price_regular_print) if q.price_regular_print else None,
        "price_regular_unit": q.price_regular_unit,
        "lead_time": q.lead_time,
        "remark": q.remark,
        "preferred_material": q.preferred_material,
        "quoted_to": q.quoted_to,
        "date_sent": str(q.date_sent) if q.date_sent else None,
    }
    return JsonResponse(data)
