import math
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20                          # por defecto
    page_query_param = "page"               # ?page=2 (default DRF)
    page_size_query_param = "page_size"     # ?page_size=50
    max_page_size = 200                     # límite superior

    def get_paginated_response(self, data):
        request = self.request
        page_size = self.get_page_size(request) or self.page_size
        total = self.page.paginator.count
        total_pages = math.ceil(total / page_size) if page_size else 1
        current_page = self.page.number

        return Response({
            "count": total,                # total de objetos (lo que quieres)
            "total_pages": total_pages,    # útil para UI
            "current_page": current_page,  # página actual
            "page_size": page_size,        # tamaño usado
            "next": self.get_next_link(),
            "previous": self.get_previous_link(),
            "results": data,
        })