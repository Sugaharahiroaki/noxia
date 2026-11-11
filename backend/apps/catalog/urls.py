from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter(trailing_slash=False)
router.register("store/courses", views.CourseViewSet, basename="course")
router.register("store/categories", views.CategoryViewSet, basename="category")
router.register("store/products", views.ProductViewSet, basename="product")
router.register("store/tables", views.TableViewSet, basename="table")

urlpatterns = router.urls
