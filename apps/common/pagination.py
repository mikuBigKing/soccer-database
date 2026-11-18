"""分页工具：统一返回 {list, total, page, page_size}。"""
from django.core.paginator import EmptyPage, Paginator


def paginate(queryset, request, default_size: int = 20, max_size: int = 100) -> dict:
    try:
        page = max(1, int(request.query_params.get('page', 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(max_size, max(1, int(request.query_params.get('page_size', default_size))))
    except (TypeError, ValueError):
        page_size = default_size

    paginator = Paginator(queryset, page_size)
    try:
        current = paginator.page(page)
    except EmptyPage:
        # 请求页超界时返回实际末页，并把 page 修正为真实页码，避免前端分页器误判
        current = paginator.page(paginator.num_pages or 1)

    return {
        'list': list(current.object_list),
        'next_page': current.next_page_number() if current.has_next() else None,
        'total': paginator.count,
        'page': current.number,
        'page_size': page_size,
    }


def paginated_response(queryset, request, serializer_class, default_size: int = 20) -> dict:
    data = paginate(queryset, request, default_size)
    data['list'] = serializer_class(data['list'], many=True).data
    return data
