from django.contrib import admin

from .models import ClosingAdjustment, DepreciationEntry, FixedAsset, InventoryClosing

admin.site.register(InventoryClosing)
admin.site.register(FixedAsset)
admin.site.register(DepreciationEntry)
admin.site.register(ClosingAdjustment)
