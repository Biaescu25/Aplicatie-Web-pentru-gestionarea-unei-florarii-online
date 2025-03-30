from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import Product, CartItem, Order, Payment, OrderItem
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages

import stripe
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt

# Set Stripe API key
stripe.api_key = settings.STRIPE_SECRET_KEY

def home(request):
    products = Product.objects.all()
    return render(request, "home.html", {'products': products})

def load_more(request):
    return render(request, "partials/more_content.html")

def cart_view(request):
    if request.user.is_authenticated:
        cart_items = CartItem.objects.filter(user=request.user)
    else:
        session_id = request.session.session_key
        if not session_id:
            request.session.create()
            session_id = request.session.session_key
        cart_items = CartItem.objects.filter(session_id=session_id)

    total_price = sum(item.total_price() for item in cart_items)

    context = {
        'cart_items': cart_items,
        'total_price': total_price
    }
    return render(request, 'cart.html', context)

def cart_count(request):
    if request.user.is_authenticated:
        count = CartItem.objects.filter(user=request.user).count()
    else:
        session_id = request.session.session_key
        if not session_id:
            request.session.create()
            session_id = request.session.session_key
        count = CartItem.objects.filter(session_id=session_id).count()
    return JsonResponse({'count': count})

def get_or_create_session_id(request):
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key

def add_to_cart(request, product_id):
    product = get_object_or_404(Product, id=product_id)

    if request.user.is_authenticated:
        cart_item, created = CartItem.objects.get_or_create(user=request.user, product=product)
    else:
        session_id = get_or_create_session_id(request)
        cart_item, created = CartItem.objects.get_or_create(session_id=session_id, product=product)

    if not created:
        cart_item.quantity += 1
        cart_item.save()

    return redirect('cart')

def remove_from_cart(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        cart_item.delete()

    if request.user.is_authenticated:
        cart_items = CartItem.objects.filter(user=request.user)
    else:
        session_id = request.session.session_key
        cart_items = CartItem.objects.filter(session_id=session_id)

    total_price = sum(item.total_price() for item in cart_items)

    if request.headers.get("HX-Request"):  # Check if it's an HTMX request
        if not cart_items:  # If cart is empty, refresh the whole page
            response = HttpResponse("")
            response["HX-Redirect"] = "/cart/"  # Redirects to the cart page
            return response

        # If cart is not empty, update both cart items and total price
        total_price_html = render_to_string("partials/total_price.html", {"total_price": total_price}, request=request)

        response = HttpResponse("")
        response["HX-Trigger"] = "updateTotalPrice"  # Trigger the total price update
        return response

    # If it's a normal request, redirect to the cart page
    return redirect("cart")

def update_cart(request, product_id, quantity):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        cart_item.quantity = quantity
        cart_item.save()
    return JsonResponse({"success": True, "total_price": cart_item.total_price()})

def increment_quantity(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id, user=request.user).first()
    if cart_item:
        cart_item.quantity += 1
        cart_item.save()

    total_price = sum(item.total_price() for item in CartItem.objects.filter(user=request.user))

    if request.headers.get('HX-Request'):
        item_html = render_to_string("partials/cart_item.html", {"cart_item": cart_item}, request=request)
        total_price_html = render_to_string("partials/total_price.html", {"total_price": total_price}, request=request)
        
        response = HttpResponse(item_html)
        response["HX-Trigger"] = "updateTotalPrice"  # Trigger total price update
        return response

    return redirect("cart")

def decrement_quantity(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id, user=request.user).first()
    if cart_item and cart_item.quantity > 1:
        cart_item.quantity -= 1
        cart_item.save()

    elif cart_item and cart_item.quantity == 1:
        #cart_item.delete()
        return remove_from_cart(request, product_id)


    total_price = sum(item.total_price() for item in CartItem.objects.filter(user=request.user))

    if request.headers.get('HX-Request'):
        item_html = render_to_string("partials/cart_item.html", {"cart_item": cart_item}, request=request) if cart_item else ""
        total_price_html = render_to_string("partials/total_price.html", {"total_price": total_price}, request=request)
        
        response = HttpResponse(item_html)
        response["HX-Trigger"] = "updateTotalPrice"  # Trigger total price update
        return response

    return redirect("cart")

def products_by_category(request, category):
    sort_order = request.GET.get('sort', 'asc')
    min_price = request.GET.get('min_price', 10)
    max_price = request.GET.get('max_price', 400)
    
    if sort_order == 'desc':
        products = Product.objects.filter(category=category, price__gte=min_price, price__lte=max_price).order_by('-price')
    else:
        products = Product.objects.filter(category=category, price__gte=min_price, price__lte=max_price).order_by('price')
    
    context = {
        'products': products,
        'category': category,
        'sort_order': sort_order,
        'min_price': min_price,
        'max_price': max_price
    }
    return render(request, "products_by_category.html", context)


def get_total_price(request):
    total_price = sum(item.total_price() for item in CartItem.objects.filter(user=request.user))
    total_price_html = render_to_string("partials/total_price.html", {"total_price": total_price}, request=request)
    return HttpResponse(total_price_html)

def register_partial(request):
    return render(request, "register.html")

def login_partial(request):
    return render(request, "login.html")

@receiver(user_logged_in)
def merge_carts(sender, request, user, **kwargs):
    session_id = request.session.session_key
    if session_id:
        guest_cart = CartItem.objects.filter(session_id=session_id, user__isnull=True)

        for item in guest_cart:
            existing_item = CartItem.objects.filter(user=user, product=item.product).first()
            if existing_item:
                existing_item.quantity += item.quantity
                existing_item.save()
                item.delete()
            else:
                item.user = user
                item.session_id = None  # Remove session association
                item.save()

def register(request):
    if request.method == "POST":
        username = request.POST["username"]
        email = request.POST["email"]
        password1 = request.POST["password1"]
        password2 = request.POST["password2"]

        # Check if passwords match
        if password1 != password2:
            messages.error(request, "Passwords do not match.")
            return redirect("register")

        # Check if username already exists
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username is already taken.")
            return redirect("register")

        # Check if email is already used
        if User.objects.filter(email=email).exists():
            messages.error(request, "Email is already in use.")
            return redirect("register")

        # Create user
        user = User.objects.create_user(username=username, email=email, password=password1)
        user.save()

        # Log the user in automatically after registration
        login(request, user)

        #messages.success(request, "Registration successful! You are now logged in.")
        return redirect("home")  # Redirect to homepage

    return render(request, "register.html")

def user_login(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            #messages.success(request, "Login successful!")
            return redirect("home")  # Redirect to homepage
        else:
            messages.error(request, "Invalid username or password.")
            return redirect("login")

    return render(request, "login.html")

def user_logout(request):
    logout(request)
    return redirect("home")  # Redirect to homepage after logout

@login_required
def checkout(request):
    cart_items = CartItem.objects.filter(user=request.user)
    subtotal = sum(item.total_price() for item in cart_items)
    delivery_fee = 10  # Example delivery fee
    total_price = subtotal + delivery_fee

    if request.method == "POST":
        order = Order.objects.create(
            user=request.user,
            full_name=request.POST["full_name"],
            email=request.POST["email"],
            address=request.POST["address"],
            phone_number=request.POST["phone_number"],
            city=request.POST["city"],
            zip_code=request.POST["zip_code"],
            total_price=subtotal,
            delivery_fee=delivery_fee,
            payment_status=False
        )

        # Save cart items to OrderItem and clear the cart
        for item in cart_items:
            OrderItem.objects.create(order=order, product=item.product, quantity=item.quantity)
        cart_items.delete()  # Clear the cart

        return redirect("process_payment")  # Redirect to payment step

    return render(request, "checkout.html", {
        "cart_items": cart_items,
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total_price": total_price,
    })

# Set Stripe API key
stripe.api_key = settings.STRIPE_SECRET_KEY

@csrf_exempt
def process_payment(request):
    if request.method == "POST":
        token = request.POST.get("stripeToken")  # Get token from Stripe.js
        amount = request.session.get("total_price", 0)  # Get total price from session

        try:
            charge = stripe.Charge.create(
                amount=int(amount * 100),  # Convert to cents
                currency="ron",
                description="Order Payment",
                source=token,
            )

            # Save Payment Info
            Payment.objects.create(
                user=request.user if request.user.is_authenticated else None,
                amount=amount,
                transaction_id=charge.id,
                status="Completed"
            )

            # Update order payment status
            Order.objects.filter(user=request.user).latest('created_at').update(payment_status=True)

            return JsonResponse({"success": True, "message": "Payment successful!"})
        except stripe.error.CardError as e:
            return JsonResponse({"success": False, "message": str(e)})
    return JsonResponse({"success": False, "message": "Invalid request"})