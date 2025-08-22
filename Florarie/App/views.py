from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponse, HttpResponseBadRequest
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.mail import send_mail, EmailMessage
from django.template.loader import render_to_string
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_GET
from django.utils import timezone
from django.conf import settings
from django.http import HttpRequest
from django.contrib.admin.views.decorators import staff_member_required
from weasyprint import HTML
from io import BytesIO
from django.utils.timezone import timedelta
from django.utils.html import format_html
from django.db.models.functions import TruncDate
from django.db.models import Sum, Count
from datetime import datetime

from .models import (
    Product, CartItem, Order, Payment, OrderItem,
    BouquetShape, Flower, Greenery, WrappingPaper, CustomBouquet, BouquetFlower, VisitorLog, WrappingPaperColor
)
from .forms import ContactForm, UserForm

import matplotlib
matplotlib.use('Agg')  # Use non-GUI backend

import stripe
stripe.api_key = settings.STRIPE_SECRET_KEY  # Set Stripe API key

import json
from PIL import Image, ImageDraw
from django.conf import settings
import math
import random

def home(request):
    all_products = Product.objects.all().order_by('-number_of_purcheses', '-created_at')

    # Filter manually using a loop
    top_products = []
    for product in all_products:
        if not product.is_custom and not product.in_store:
            top_products.append(product)
        if len(top_products) == 3:
            break

    cart_product_ids = get_cart_items(request).values_list('product_id', flat=True)

    return render(request, "home.html", {'products': top_products, 'cart_product_ids': cart_product_ids})

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

    # Limit quantity to stock
    current_quantity = cart_item.quantity if not created else 0
    if current_quantity < product.stock:
        cart_item.quantity = current_quantity + 1
        cart_item.save()
    # else: do not add more than stock

    # Check if the request is from HTMX
    if request.headers.get("HX-Request"):
        cart_count = get_cart_items(request).count()
        html = render_to_string("partials/cart_count.html", {"cart_count": cart_count})
        return HttpResponse(html)
    
    return redirect("cart")

def remove_from_cart(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        if cart_item.product.bid_submited:
            # If the product is in auction, set bid_submited to False
            cart_item.product.bid_submited = False
            cart_item.product.price = cart_item.product.before_auction_price  # Reset to original price
            cart_item.product.save() 
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
    
        cart_count = get_cart_items(request).count()
        cart_html = render_to_string("partials/cart_count.html", {"cart_count": cart_count}, request=request)

        # Return a response that updates the cart count (via hx-swap-oob)
        response = HttpResponse(cart_html)

        #response = HttpResponse("")
        response["HX-Trigger"] = "updateTotalPrice"  # Trigger the total price update
        return response


    # If it's a normal request, redirect to the cart page
    return redirect("cart")

def update_cart(request, product_id, quantity):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        # Only allow up to product stock
        cart_item.quantity = min(quantity, cart_item.product.stock)
        cart_item.save()
    return JsonResponse({"success": True, "total_price": cart_item.total_price()})

def increment_quantity(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id, user=request.user).first()
    if cart_item:
        # For buchete and aranjamente, allow up to 10 without stock check
        if cart_item.product.category in ['buchete', 'aranjamente']:
            if cart_item.quantity < 10:
                cart_item.quantity += 1
                cart_item.save()
        else:
            # For other products, check against stock
            if cart_item.quantity < cart_item.product.stock:
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


def products_by_category(request: HttpRequest, category):
    products = Product.objects.filter(category=category)

    # Determine smallest and largest prices
    smallest_price = products.order_by('price').first().price if products.exists() else 0
    largest_price = products.order_by('-price').first().price if products.exists() else 0

    # Apply price range filter
    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    if min_price and max_price:
        products = products.filter(price__gte=min_price, price__lte=max_price)

    # Apply sorting
    sort_order = request.GET.get('sort', 'asc')
    if sort_order == 'asc':
        products = products.order_by('price')
    elif sort_order == 'desc':
        products = products.order_by('-price')

    # Mark products as new (added in last 48 hours)
    now = timezone.now()
    new_threshold = now - timedelta(hours=48)
    products = list(products)  # Materialize queryset

    for product in products:
        product.is_new = product.created_at >= new_threshold


    cart_product_ids = get_cart_items(request).values_list('product_id', flat=True)

    context = {
        'products': products,
        'category': category,
        'sort_order': sort_order,
        'smallest_price': smallest_price,
        'largest_price': largest_price,
        'cart_product_ids': cart_product_ids
    }

    if request.headers.get('HX-Request'):
        return render(request, 'partials/ProductShowcase.html', context)
    else:
        return render(request, 'products_by_category.html', context)


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

        # Password restrictions
        errors = []
        if len(password1) < 8:
            errors.append("Password must be at least 8 characters long.")
        if not any(c.isdigit() for c in password1):
            errors.append("Password must contain at least one digit.")
        if not any(c.isalpha() for c in password1):
            errors.append("Password must contain at least one letter.")
        if not any(c.isupper() for c in password1):
            errors.append("Password must contain at least one uppercase letter.")
        if not any(c.islower() for c in password1):
            errors.append("Password must contain at least one lowercase letter.")

        # Check if passwords match
        if password1 != password2:
            errors.append("Passwords do not match.")

        # Check if username already exists
        if User.objects.filter(username=username).exists():
            errors.append("Username is already taken.")

        # Check if email is already used
        if User.objects.filter(email=email).exists():
            errors.append("Email is already in use.")

        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect("register")

        # Create user
        user = User.objects.create_user(username=username, email=email, password=password1)
        user.save()

        # Log the user in automatically after registration
        from django.contrib.auth import authenticate
        user = authenticate(request, username=username, password=password1)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')

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

def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    related_products = Product.objects.filter(category=product.category).exclude(pk=pk)[:10]
    cart_product_ids = get_cart_items(request).values_list('product_id', flat=True)

    context = {
        'product': product,
        'related_products': related_products,
        'cart_product_ids': cart_product_ids
    }
    return render(request, 'product_detail.html', context)

@login_required
def profile(request):
   
    user = request.user
    if request.method == 'POST':
        form = UserForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            return redirect('profile')  
    else:
        form = UserForm(instance=user)

    return render(request, 'profile.html', {'form': form})

@login_required
def order_history(request):
    orders = Order.objects.filter(user=request.user).order_by('-created_at')
    paginator = Paginator(orders, 5)
    page_number = request.GET.get('page')
    page_orders = paginator.get_page(page_number)

    if request.htmx:
        html = render_to_string('partials/order_history.html', {'orders': page_orders}, request=request)
        return HttpResponse(html)

    return redirect('profile')

@login_required
def order_detail(request, order_id):
    order = get_object_or_404(Order, id=order_id, user=request.user)
    order_items = order.items.select_related('product')  

    return render(request, 'order_detail.html', {
        'order': order,
        'order_items': order_items,
    })


stripe.api_key = settings.STRIPE_SECRET_KEY  # Set Stripe API key

@login_required
def checkout(request):
    """ Handles checkout process and creates order in DB. """
    user = request.user if request.user.is_authenticated else None
    session_id = request.session.session_key or request.session.create()
    cart_items = CartItem.objects.filter(user=user) if user else CartItem.objects.filter(session_id=session_id)

    subtotal = sum(item.total_price() for item in cart_items)
    delivery_fee = 10  # Example delivery fee
    total_price = subtotal + delivery_fee

    if request.method == "POST":
        payment_method = request.POST["payment_method"]

        # Create the order
        order = Order.objects.create(
            user=user,
            session_id=session_id if not user else None,
            full_name=request.POST["full_name"],
            email=request.POST["email"],
            address=request.POST["address"],
            phone_number=request.POST["phone_number"],
            city=request.POST["city"],
            zip_code=request.POST["zip_code"],
            total_price=subtotal,
            delivery_fee=delivery_fee,
            payment_method=payment_method,
            payment_status=False if payment_method == "card" else True
        )

        # Save cart items to OrderItem
        for item in cart_items:
            price = item.product.price if item.product.price is not None else 0  # Ensure price is not None
            OrderItem.objects.create(order=order, product=item.product, quantity=item.quantity, price=price)

        # Clear the cart after saving order
        cart_items.delete()

        if payment_method == "card":
            request.session["order_id"] = order.id  # Store order ID for payment processing
            return redirect("process_payment")  # Redirect to Stripe payment

        return redirect("order_success")  # Redirect to success page for cash on delivery

    return render(request, "checkout.html", {
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
        "cart_items": cart_items,
        "subtotal": subtotal,
        "delivery_fee": delivery_fee,
        "total_price": total_price,
    })

def checkout_step_1(request):
    return render(request, 'checkout_step_1.html')

def get_cart_items(request):
    user = request.user if request.user.is_authenticated else None
    session_id = request.session.session_key or request.session.create()
    return CartItem.objects.filter(user=user) if user else CartItem.objects.filter(session_id=session_id)

def checkout_step_2(request):
    if request.method == 'POST':
        request.session['checkout_data'] = {
            'full_name': request.POST.get('full_name'),
            'email': request.POST.get('email'),
            'phone_number': request.POST.get('phone_number'),
            'address': request.POST.get('address'),
            'city': request.POST.get('city'),
            'zip_code': request.POST.get('zip_code'),
            'payment_method': request.POST.get('payment_method'),
        }

        # Calculate total here just like in `checkout()`
        user = request.user if request.user.is_authenticated else None
        session_id = request.session.session_key or request.session.create()

        cart_items = get_cart_items(request)
        subtotal = sum(item.total_price() for item in cart_items)
        delivery_fee = 10
        total_price = subtotal + delivery_fee

        # Save total price for payment
        request.session["total_price"] = float(total_price)

        # Create the Order
        order = Order.objects.create(
            user=user,
            session_id=None if user else session_id,
            full_name=request.POST.get("full_name"),
            email=request.POST.get("email"),
            address=request.POST.get("address"),
            phone_number=request.POST.get("phone_number"),
            city=request.POST.get("city"),
            zip_code=request.POST.get("zip_code"),
            total_price=subtotal,
            delivery_fee=delivery_fee,
            payment_method=request.POST.get("payment_method"),
            payment_status=False  # Will update after payment
        )

        # Save order ID in session for later update
        request.session["order_id"] = order.id

        # Save CartItems to OrderItems
        for item in cart_items:
            OrderItem.objects.create(order=order, product=item.product, quantity=item.quantity, price=item.product.price)

        return render(request, 'checkout_step_2.html', {
            'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
        })


    return redirect('checkout_step_1')

@csrf_exempt
def checkout_step_3(request):
    if request.method == 'POST':
        token = request.POST.get('stripeToken')
        amount = float(request.session.get("total_price", 0))

        print("Received token:", token)  # Debugging
        print("Amount:", amount)  # Debugging

        if not token or amount == 0:
            return JsonResponse({"success": False, "message": "Missing payment token or invalid amount."})

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
            try:
                order_id = request.session.get("order_id")
                order = Order.objects.get(id=order_id)
                order.payment_status = True
                order.save()

                send_order_email(order, request.user)

                # Decrement stock for each product in the order
                for item in order.items.all():
                    if item.product.stock is not None and item.product.stock > 0:
                        item.product.stock = max(0, item.product.stock - item.quantity)
                        item.product.save()

                # Clear the cart after successful payment
                user = request.user if request.user.is_authenticated else None
                session_id = request.session.session_key
                cart_items = CartItem.objects.filter(user=user) if user else CartItem.objects.filter(session_id=session_id)

                for item in cart_items:
                    item.product.number_of_purcheses += item.quantity
                    item.product.save()

                cart_items.delete()
            except Order.DoesNotExist:
                return JsonResponse({"success": False, "message": "No matching order found."})

            #return JsonResponse({"success": True, "message": "Payment successful!"})
            if request.headers.get("HX-Request"):
                return render(request, 'checkout_step_3.html')
            else:
                return redirect("order_success")  # fallback if not HTMX
        
        except stripe.error.CardError as e:
            return JsonResponse({"success": False, "message": f"Card error: {str(e)}"})
        except stripe.error.StripeError as e:
            return JsonResponse({"success": False, "message": f"Stripe error: {str(e)}"})
        except Exception as e:
            return JsonResponse({"success": False, "message": f"Unexpected error: {str(e)}"})

    return JsonResponse({"success": False, "message": "Invalid request"})

def custom_bouquet_builder(request):
    shape = BouquetShape.objects.all()
    wrapping = WrappingPaper.objects.filter(in_stock=True).prefetch_related("color_variants__color")
    greenery = Greenery.objects.all()
    flower = Flower.objects.all()
       
    return render(request, "custom_bouquet_builder.html", {
            "shapes": shape,
            "wrappings": wrapping,
            "greens": greenery,
            "flowers": flower,
        })

def create_custom_bouquet(request):
    if request.method == "POST" and request.headers.get("HX-Request"):
        data = request.POST

        try:
            shape = BouquetShape.objects.get(id=data.get("shape"))
        except BouquetShape.DoesNotExist:
            # Render the summary
            return render(request, "custom_bouquet_summary.html", {
                "error": "Please select the shape ."
            })

        # Wrapping
        wrapping_ids = data.getlist("wrapping")
        wrapping_list = WrappingPaper.objects.filter(id__in=wrapping_ids)
        wrapping_price = sum(w.price for w in wrapping_list)

        # Greenery
        greenery_ids = data.getlist("greens")
        greenery_list = Greenery.objects.filter(id__in=greenery_ids)
        greenery_price = sum(g.price for g in greenery_list)

        # Flower selection
        flower_summary = []
        total_flower_price = 0
        qty = 0
        numflowers = 0

        for flower in Flower.objects.all():
            qty = int(data.get(f"flower_{flower.id}", 0))
            if qty > 0:
                subtotal = flower.price * qty
                flower_summary.append({
                    "name": flower.name,
                    "quantity": qty,
                    "subtotal": subtotal,
                })
                total_flower_price += subtotal
                numflowers += 1

        if numflowers == 0:
            # Render the summary
            return render(request, "custom_bouquet_summary.html", {
                "error": "Please select at least one flower."
            })
        

        # Calculate total price
        total_price = wrapping_price + greenery_price + total_flower_price

        color_name = data.get("wrapping_color_name", "")

        # Render the summary
        return render(request, "custom_bouquet_summary.html", {
            "shape": shape,
            "wrapping": wrapping_list,
            "greens": greenery_list,
            "flower_summary": flower_summary,
            "total_price": total_price,
            "color": color_name,
        })
    return JsonResponse({"error": "Invalid request"}, status=400)

@csrf_exempt
def save_custom_bouquet(request):
    if request.method == "POST":
        data = request.POST

        try:
            shape = BouquetShape.objects.get(id=data.get("shape"))
        except BouquetShape.DoesNotExist:
            return JsonResponse({"error": "Invalid shape selected."}, status=400)

        # WrappingPaperColor (variantă hârtie + culoare)
        variant_id = data.get("wrapping_variant_id")
        try:
            wrapping_variant = WrappingPaperColor.objects.select_related("wrapping_paper").get(id=variant_id)
        except WrappingPaperColor.DoesNotExist:
            return JsonResponse({"error": "Hârtia selectată nu este validă."}, status=400)

        # Verifică stoc
        if wrapping_variant.quantity < 1:
            return JsonResponse({"error": "Stoc insuficient pentru hârtia selectată."}, status=400)

        # Scade stocul
        wrapping_variant.quantity -= 1
        wrapping_variant.save()

        # Greenery
        greenery_ids = data.getlist("greens")
        greenery_list = Greenery.objects.filter(id__in=greenery_ids)

        # Flowers
        flower_quantities = {}
        for flower in Flower.objects.all():
            qty = int(data.get(f"flower_{flower.id}", 0))
            if qty > 0:
                flower_quantities[flower.id] = qty

        # Calculate total price
        total_price = float(data.get("total_price", 0))

        # Creează buchetul
        custom_bouquet = CustomBouquet.objects.create(
            user=request.user if request.user.is_authenticated else None,
            shape=shape,
            wrapping=wrapping_variant.wrapping_paper,
        )
        custom_bouquet.greenery.set(greenery_list)

        # Adaugă florile
        for flower_id, quantity in flower_quantities.items():
            BouquetFlower.objects.create(
                bouquet=custom_bouquet,
                flower_id=flower_id,
                quantity=quantity
            )

        # Creează produsul corespunzător
        custom_product = Product.objects.create(
            name=f"Buchet personalizat #{custom_bouquet.id}",
            price=total_price,
            is_custom=True,
            image='Logo.png',
            category='CustomBouquet'  
        )

        # Asociază produsul cu buchetul
        custom_bouquet.product = custom_product
        custom_bouquet.save()

        # Adaugă în coș
        if request.user.is_authenticated:
            cart_item, _ = CartItem.objects.get_or_create(user=request.user, product=custom_product)
        else:
            session_id = get_or_create_session_id(request)
            cart_item, _ = CartItem.objects.get_or_create(session_id=session_id, product=custom_product)

        cart_item.quantity = 1
        cart_item.save()

        # Redirect către coș
        response = HttpResponse()
        response["HX-Redirect"] = "/cart/"
        return response

    return JsonResponse({"error": "Invalid request"}, status=400)



@csrf_exempt
def generate_bouquet_preview(request):
    if request.method != "POST":
        return HttpResponse(status=405)

    data = json.loads(request.body)
    
    shape_id = data.get("shape")
    wrapping_id = data.get("wrapping")

    if not shape_id or not str(shape_id).isdigit():
        return JsonResponse({"error": "Forma buchetului este invalidă."}, status=400)

    if not wrapping_id or not str(wrapping_id).isdigit():
        return JsonResponse({"error": "Ambalajul este invalid."}, status=400)

    try:
        shape = BouquetShape.objects.get(id=int(shape_id))
        wrapping = WrappingPaper.objects.get(id=int(wrapping_id))
    except (BouquetShape.DoesNotExist, WrappingPaper.DoesNotExist):
        return JsonResponse({"error": "Datele introduse nu sunt valide."}, status=400)

    flowers_data = data.get("flowers", [])

    canvas_size = (500, 500)
    center = (canvas_size[0] // 2, canvas_size[1] // 2)
    base_radius = 120  # inner circle radius
    radius_step = 50   # distance between circles

    # Creează canvas
    image = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)

    # Desenează cercul foliei
    color_hex = data.get("wrapping_color", "#FFFFFF")
    draw.ellipse([
        (center[0] - base_radius, center[1] - base_radius),
        (center[0] + base_radius, center[1] + base_radius)
    ], fill=color_hex)

    #Construim lista completă cu toate florile + numărul lor
    all_flowers = []
    # Add flowers
    for flower_data in flowers_data:
        try:
            flower = Flower.objects.get(id=flower_data["id"])
            count = int(flower_data["count"])
            for _ in range(count):
                all_flowers.append(flower)
        except:
            continue

    # Add greenery (if provided in data)
    greenery_data = data.get("greenery", [])
    for green_data in greenery_data:
        try:
            green = Greenery.objects.get(id=green_data["id"])
            #count = int(green_data["count"])
            count = int(flower_data["count"]) * 0.05
            for _ in range(count):
                all_flowers.append(green)
        except:
            continue

    num_flowers = len(all_flowers)
    if num_flowers == 0:
        return HttpResponse(status=400)

    circle_max = [1, 8, 12, 16, 20, 24]
    flower_positions = []
    flower_idx = 0
    random.shuffle(all_flowers)

    base_size = 300
    min_size = 50
    shrink_step = 18  # pixels to shrink per circle

    for circle_num, max_on_circle in enumerate(circle_max):
        if flower_idx >= num_flowers:
            break
        flowers_in_this_circle = min(max_on_circle, num_flowers - flower_idx)
        # Calculate flower size for this circle (same as rendering logic)
        if circle_num == 0:
            size = max(min_size, base_size - int((base_size - min_size) * min(num_flowers, 50) / 50))
            # Center flower
            flower = all_flowers[flower_idx]
            flower_positions.append((center[1], center[0], flower, size, "flower"))
            flower_idx += 1
        else:
            # Calculate flower size for this circle (shrinks as number of flowers increases)
            size = max(min_size, base_size - int((base_size - min_size) * num_flowers / 50)) 

            max_tight_radius = base_radius + (len(circle_max) - 1) * radius_step
            min_tight_radius = base_radius + (len(circle_max) - 1) * radius_step // 2
            # Interpolate between max_tight_radius and min_tight_radius based on num_flowers
            tight_radius = int(
                max_tight_radius - (max_tight_radius - min_tight_radius) * min(num_flowers, 60) / 60
            )
            # Use tight_radius for the outermost circle, otherwise normal calculation
            if circle_num == len(circle_max) - 1:
                radius = tight_radius - (base_size - size)
            else:
                radius = base_radius + circle_num * radius_step - (base_size - size)
            angle_step = 360 / flowers_in_this_circle if flowers_in_this_circle > 0 else 360
            for i in range(flowers_in_this_circle):
                flower = all_flowers[flower_idx]
                angle = math.radians(i * angle_step)
                r = radius + random.randint(-10, 10)
                x = int(center[0] + r * math.cos(angle))
                y = int(center[1] + r * math.sin(angle))
                flower_positions.append((y, x, flower, size, "flower"))
                flower_idx += 1
                if flower_idx >= num_flowers:
                    break

    # Insert greenery images among the flowers on the circles
    # if greenery_images:
    #     positions_for_greenery = [i for i, pos in enumerate(flower_positions) if pos[4] == "flower" and pos[0] != center[1] and pos[1] != center[0]]
    #     if positions_for_greenery:
    #         step = max(1, len(positions_for_greenery) // len(greenery_images))
    #         for idx, green_img in enumerate(greenery_images):
    #             pos_idx = positions_for_greenery[idx * step % len(positions_for_greenery)]
    #             y, x, _, size, _ = flower_positions[pos_idx]
    #             flower_positions.insert(pos_idx, (y, x, green_img, size, "greenery"))
        # else: do not insert greenery if there are no positions available

    # Sort positions by y (ascending), then x (ascending) to avoid comparing Flower/Image objects
    flower_positions.sort(key=lambda tup: (tup[0], tup[1]))

    for y, x, obj, size, obj_type in flower_positions:
        try:
            if obj_type == "flower":
                flower_img = Image.open(obj.image.path).convert("RGBA") if hasattr(obj, "image") else obj
                flower_img = flower_img.resize((size, size), Image.LANCZOS)
                bottom_center_x = center[0]
                bottom_center_y = canvas_size[1]
                dx = bottom_center_x - x
                dy = bottom_center_y - y
                angle_rad = math.atan2(dx, dy)
                angle_deg = math.degrees(angle_rad)
                rotated_img = flower_img.rotate(angle_deg, expand=True)
            else:  # greenery
                rotated_img = obj  # greenery already resized, no rotation for simplicity

            rx, ry = rotated_img.size
            image.paste(rotated_img, (x - rx // 2, y - ry // 2), rotated_img)
        except:
            continue

    # După ce ai lipit toate florile:
    # folie_path = os.path.join(settings.BASE_DIR, 'App', 'static', 'folie1.png')
    # print(f"Calea foliei: {folie_path}")

    try:
        wrapping_img = Image.open(folie_path).convert("RGBA")
        wrapping_img = wrapping_img.resize(canvas_size, Image.LANCZOS)

        # Ajustează opacitatea
        alpha = wrapping_img.split()[3]
        alpha = alpha.point(lambda p: int(p * 0.5))  # 50% opacitate
        wrapping_img.putalpha(alpha)

        # Combină imaginile
        image = Image.alpha_composite(image, wrapping_img)

    except Exception as e:
        print(f"Eroare la suprapunerea foliei: {e}")

        # Returnează imaginea finală
    response = HttpResponse(content_type="image/png")
    image.save(response, "PNG")
    return response

def auction_view(request):
    products = Product.objects.all()

    # Check for expired reservations
    for product in products.filter(bid_submited=True):
        cart_items = CartItem.objects.filter(product=product)
        for cart_item in cart_items:
            if cart_item.is_expired():
                cart_item.delete()
                product.bid_submited = False
                product.save()

    auction_products = [p for p in products if p.is_in_auction()]

    if request.headers.get("HX-Request"):
        return render(request, "partials/auction_list.html", {"products": auction_products})

    return render(request, "auction.html", {"products": auction_products})

def auction_price_partial(request, pk):
    product = get_object_or_404(Product, pk=pk)
    return render(request, "partials/auction_price.html", {"product": product})

def  auction_confirm_popup(request, pk):
    if not request.headers.get("HX-Request"):
        return HttpResponseBadRequest("Invalid request")
    
    print("auction_confirm_popup called")
    product = get_object_or_404(Product, pk=pk)
     # GET request: show confirmation popup
    return render(request, "partials/auction_confirm_popup.html", {"product": product})                      

@require_POST
def auction_confirm(request, pk):

    product = get_object_or_404(Product, pk=pk)

    if request.user.is_authenticated:
        cart_item, created = CartItem.objects.get_or_create(user=request.user, product=product)
    else:
        session_id = get_or_create_session_id(request)
        cart_item, created = CartItem.objects.get_or_create(session_id=session_id, product=product)

    if product.is_in_auction():
        auction_price, _, _ = product.get_auction_price()
        cart_item.product.price = auction_price
        cart_item.product.bid_submited = True
        cart_item.reserved_until = timezone.now() + timedelta(minutes=10)  # rezervare 10 min
        cart_item.save()
        cart_item.product.save()

    return redirect("auction")


def send_order_email(order, user):
    subject = f"Order Confirmation - #{order.id}"
    html_message = render_to_string("emails/order_confirmation_email.html", {"order": order, "user": user})
    from_email = settings.DEFAULT_FROM_EMAIL
    to_email = [user.email]

    # Generate PDF invoice
    invoice_html = render_to_string('emails/invoice.html', {'order': order, 'user': user})
    pdf_file = BytesIO()
    HTML(string=invoice_html).write_pdf(pdf_file)

    # Create the email with HTML content and PDF attachment
    email = EmailMessage(
        subject=subject,
        body=html_message,  # Use full HTML content here
        from_email=from_email,
        to=to_email,
    )
    email.content_subtype = "html"  # Mark content as HTML
    email.attach(f"invoice_{order.id}.pdf", pdf_file.getvalue(), 'application/pdf')
    email.send()


from django.utils import timezone

def contact_view(request):
    form = ContactForm(request.POST or None)

    if form.is_valid():
        contact_message = form.save(commit=False)
        contact_message.submitted_at = timezone.now()
        contact_message.save()

        # Email către administrator
        subject = f"New Contact Message from {contact_message.name}"
        from_email = contact_message.email
        to_email = [settings.DEFAULT_FROM_EMAIL]

        message_html = format_html(
            "<b>Nume:</b> {}<br>"
            "<b>Email:</b> {}<br>"
            "<b>Mesaj:</b> {}<br>"
            "<b>Trimis la:</b> {}",
            contact_message.name,
            contact_message.email,
            contact_message.message,
            timezone.localtime(contact_message.submitted_at).strftime('%d-%m-%Y %H:%M:%S')
        )

        send_mail(
            subject,
            '', 
            from_email,
            to_email,
            fail_silently=False,
            html_message=message_html  
        )

        return redirect('contact_success')

    return render(request, 'contact.html', {'form': form})

def contact_success(request):

    return render(request, 'contact_success.html')


@staff_member_required
def admin_dashboard(request):
    categories = Product.objects.values_list('category', flat=True).distinct()

    # Orders and revenue for this period (last 30 days)
    today = datetime.today().date()
    start_date = today - timedelta(days=30)
    prev_start = start_date - timedelta(days=30)
    prev_end = start_date

    orders_qs = Order.objects.filter(created_at__date__range=(start_date, today))
    prev_orders_qs = Order.objects.filter(created_at__date__range=(prev_start, prev_end))

    total_orders = orders_qs.count()
    prev_orders = prev_orders_qs.count()
    orders_growth = ((total_orders - prev_orders) / prev_orders * 100) if prev_orders else None

    total_revenue = orders_qs.aggregate(total=Sum('total_price'))['total'] or 0
    prev_revenue = prev_orders_qs.aggregate(total=Sum('total_price'))['total'] or 0
    revenue_growth = ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue else None

    return render(request, "admin_dashboard.html", {
        "categories": categories,
        "total_orders": total_orders,
        "orders_growth": orders_growth,
        "total_revenue": total_revenue,
        "revenue_growth": revenue_growth,
    })

def product_list_api(request):
    category = request.GET.get("category")
    qs = Product.objects.all()
    if category:
        qs = qs.filter(category=category)
    products = list(qs.values("id", "name"))
    return JsonResponse({"products": products})

def sales_data_api(request):
    start = request.GET.get("start")
    end = request.GET.get("end")
    category = request.GET.get("category")
    product = request.GET.get("product")
    user_segment = request.GET.get("user_segment")
    compare_start = request.GET.get("compare_start")
    compare_end = request.GET.get("compare_end")

    today = datetime.today().date()
    start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else today - timedelta(days=30)
    end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else today

    orders = Order.objects.filter(created_at__date__range=(start_date, end_date))

    # User segmentation
    if user_segment == "new":
        orders = orders.filter(user__date_joined__gte=start_date)
    elif user_segment == "returning":
        orders = orders.filter(user__date_joined__lt=start_date)
    elif user_segment == "region":
        # Example: filter by city (region)
        region = request.GET.get("region")
        if region:
            orders = orders.filter(city=region)

    # Category/Product filter
    if category:
        orders = orders.filter(items__product__category=category)
    if product:
        orders = orders.filter(items__product__id=product)

    # Sales over time
    daily_sales = (
        orders.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Sum("total_price"), count=Count("id"))
        .order_by("day")
    )
    labels = [d["day"].strftime("%Y-%m-%d") for d in daily_sales]
    totals = [float(d["total"]) for d in daily_sales]
    counts = [d["count"] for d in daily_sales]

    # Top products
    product_qs = Product.objects.filter(orderitem__order__in=orders)
    if category:
        product_qs = product_qs.filter(category=category)
    top_products = (
        product_qs
        .annotate(sold=Sum("orderitem__quantity"))
        .order_by("-sold")[:5]
    )
    top_product_names = [p.name for p in top_products]
    top_product_sold = [p.sold or 0 for p in top_products]

    # Visits
    visits = (
        VisitorLog.objects
        .filter(timestamp__date__range=(start_date, end_date))
        .annotate(date=TruncDate('timestamp'))
        .values('date')
        .annotate(count=Count('id'))
        .order_by('date')
    )
    visits_data = {v['date'].strftime("%Y-%m-%d"): v['count'] for v in visits}
    visit_counts = [visits_data.get(label, 0) for label in labels]

    # Conversion rate per day (as percentage)
    conversion_rates = []
    for sales_count, visit_count in zip(counts, visit_counts):
        if visit_count > 0:
            conversion_rates.append(round((sales_count / visit_count) * 100, 2))
        else:
            conversion_rates.append(0)

    # Compare periods
    compare = None
    if compare_start and compare_end:
        cs = datetime.strptime(compare_start, "%Y-%m-%d").date()
        ce = datetime.strptime(compare_end, "%Y-%m-%d").date()
        compare_orders = Order.objects.filter(created_at__date__range=(cs, ce))
        compare_daily_sales = (
            compare_orders.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(total=Sum("total_price"), count=Count("id"))
            .order_by("day")
        )
        compare_labels = [d["day"].strftime("%Y-%m-%d") for d in compare_daily_sales]
        compare_totals = [float(d["total"]) for d in compare_daily_sales]
        compare = {
            "labels": compare_labels,
            "totals": compare_totals,
        }

    return JsonResponse({
        "sales": {
            "labels": labels,
            "totals": totals,
            "counts": counts,
        },
        "top_products": {
            "labels": top_product_names,
            "data": top_product_sold,
        },
        "visits": {
            "labels": labels,
            "counts": visit_counts,
        },
        "conversion": {
            "labels": labels,
            "rates": conversion_rates,
        },
        "compare": compare,
    })

@require_GET
def sales_summary_api(request):
    start = request.GET.get("start")
    end = request.GET.get("end")
    today = datetime.today().date()
    start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else today - timedelta(days=30)
    end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else today

    prev_start = start_date - (end_date - start_date)
    prev_end = start_date

    orders_qs = Order.objects.filter(created_at__date__range=(start_date, end_date))
    prev_orders_qs = Order.objects.filter(created_at__date__range=(prev_start, prev_end))

    total_orders = orders_qs.count()
    prev_orders = prev_orders_qs.count()
    orders_growth = ((total_orders - prev_orders) / prev_orders * 100) if prev_orders else None

    total_revenue = orders_qs.aggregate(total=Sum('total_price'))['total'] or 0
    prev_revenue = prev_orders_qs.aggregate(total=Sum('total_price'))['total'] or 0
    revenue_growth = ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue else None

    # Format total_revenue to 2 decimal places
    total_revenue = round(float(total_revenue), 2)

    return JsonResponse({
        "total_orders": total_orders,
        "orders_growth": orders_growth,
        "total_revenue": total_revenue,
        "revenue_growth": revenue_growth,
    })

