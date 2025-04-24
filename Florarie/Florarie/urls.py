"""
URL configuration for Florarie project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.conf import settings
from django.urls import path, include
from django.conf.urls.static import static
from App.views import home, cart_view, add_to_cart, remove_from_cart, update_cart, cart_count, products_by_category
from App.views import increment_quantity, decrement_quantity, get_total_price, register_partial, login_partial,register, user_login, user_logout
from App.views import checkout, product_detail, profile, order_detail, order_history, checkout_step_1, checkout_step_2, checkout_step_3, create_custom_bouquet, custom_bouquet_builder
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', home, name='home'),
    path('cart/', cart_view, name='cart'),
    path('cart/add/<int:product_id>/', add_to_cart, name='add_to_cart'),
    path('cart/remove/<int:product_id>/', remove_from_cart, name='remove_from_cart'),
    path('cart/update/<int:product_id>/<int:quantity>/', update_cart, name='update_cart'),
    path('cart/count/', cart_count, name='cart_count'),
    path('category/<str:category>/',products_by_category, name='products_by_category'),
    path('cart/increment/<int:product_id>/', increment_quantity, name='increment_quantity'),
    path('cart/decrement/<int:product_id>/', decrement_quantity, name='decrement_quantity'),
    path("get-total-price/", get_total_price, name="get_total_price"),
    path('register_page/', register_partial, name='register_page'),
    path('login_page/', login_partial, name='login_page'),
    path("register/", register, name="register"),
    path("login/", user_login, name="login"),
    path("logout/", user_logout, name="logout"),
    path("checkout/", checkout, name="checkout"),   
    path('product/<int:pk>/', product_detail, name='product_detail'),
    path('profile/', profile, name='profile'),
    path('order/<int:order_id>/', order_detail, name='order_detail'),
    #path('order/<int:order_id>/reorder/', reorder, name='reorder'),
    path('password_change/', auth_views.PasswordChangeView.as_view(template_name='password_change.html'), name='password_change'),
    path('password_change/done/', auth_views.PasswordChangeDoneView.as_view(template_name='password_change_done.html'), name='password_change_done'),
    path('profile/orders/', order_history, name='order_history'),
    path('checkout/step-1/', checkout_step_1, name='checkout_step_1'),
    path('checkout/step-2/', checkout_step_2, name='checkout_step_2'),
    path('checkout/step-3/', checkout_step_3, name='checkout_step_3'),
    path('custom-bouquet/', custom_bouquet_builder, name='custom_bouquet_builder'),
    path("custom-bouquet/create/", create_custom_bouquet, name="create_custom_bouquet"),
]


if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)