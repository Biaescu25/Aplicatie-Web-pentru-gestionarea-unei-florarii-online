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
    BouquetShape, Flower, Greenery, WrappingPaper, WrappingColor, CustomBouquet, BouquetFlower, VisitorLog, WrappingPaperColor
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

    # Limit quantity to stock (except for buchete, aranjamente, and custom bouquets)
    current_quantity = cart_item.quantity if not created else 0
    if product.category in ['buchete', 'aranjamente', 'CustomBouquet']:
        # For these categories, allow up to 10 without stock check
        if current_quantity < 10:
            cart_item.quantity = current_quantity + 1
            cart_item.save()
    else:
        # For other products, check against stock
        if current_quantity < product.stock:
            cart_item.quantity = current_quantity + 1
            cart_item.save()

    # Check if the request is from HTMX
    if request.headers.get("HX-Request"):
        cart_count = get_cart_items(request).count()
        html = render_to_string("partials/cart_count.html", {"cart_count": cart_count})
        return HttpResponse(html)
    
    return redirect("cart")

def remove_from_cart(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        # Check if this is a custom bouquet product
        if cart_item.product.is_custom and cart_item.product.category == 'CustomBouquet':
            # Delete the custom bouquet and its associated product
            try:
                custom_bouquet = CustomBouquet.objects.get(product=cart_item.product)
                # Delete the product first (which will cascade to cart items)
                cart_item.product.delete()
                # Delete the custom bouquet
                custom_bouquet.delete()
            except CustomBouquet.DoesNotExist:
                # If no custom bouquet found, just delete the product
                cart_item.product.delete()
        else:
            # For regular products, handle auction logic
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
        # For buchete, aranjamente, and custom bouquets, allow up to 10
        if cart_item.product.category in ['buchete', 'aranjamente', 'CustomBouquet']:
            cart_item.quantity = min(quantity, 10)
        else:
            # For other products, check against stock
            cart_item.quantity = min(quantity, cart_item.product.stock)
            cart_item.save()
    return JsonResponse({"success": True, "total_price": cart_item.total_price()})

def increment_quantity(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id, user=request.user).first()
    if cart_item:
        # For buchete, aranjamente, and custom bouquets, allow up to 10 without stock check
        if cart_item.product.category in ['buchete', 'aranjamente', 'CustomBouquet']:
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
            errors.append("Parola trebuie să aibă cel puțin 8 caractere.")
        if not any(c.isdigit() for c in password1):
            errors.append("Parola trebuie să conțină cel puțin o cifră.")
        if not any(c.isalpha() for c in password1):
            errors.append("Parola trebuie să conțină cel puțin o literă.")
        if not any(c.isupper() for c in password1):
            errors.append("Parola trebuie să conțină cel puțin o literă mare.")
        if not any(c.islower() for c in password1):
            errors.append("Parola trebuie să conțină cel puțin o literă mică.")

        # Check if passwords match
        if password1 != password2:
            errors.append("Parolele nu se potrivesc.")

        # Check if username already exists
        if User.objects.filter(username=username).exists():
            errors.append("Username este deja folosit.")

        # Check if email is already used
        if User.objects.filter(email=email).exists():
            errors.append("Email este deja folosit.")

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
            messages.error(request, "Credențiale invalide.")
            return redirect("login")

    return render(request, "login.html")

def user_logout(request):
    logout(request)
    return redirect("home")  # Redirect to homepage after logout

def product_detail(request, pk):
    product = get_object_or_404(Product, pk=pk)
    related_products = Product.objects.filter(category=product.category).exclude(pk=pk)
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
    delivery_fee = 29  # Default delivery fee for address delivery
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
        # Get form data
        delivery_type = request.POST.get('delivery_type', 'delivery')
        payment_method = request.POST.get('payment_method')
        desired_delivery_date = request.POST.get("desired_delivery_date")
        
        # Validate delivery date (must be at least 48 hours from now)
        if desired_delivery_date:
            from datetime import datetime, timedelta
            try:
                delivery_date = datetime.strptime(desired_delivery_date, '%Y-%m-%d').date()
                min_date = (datetime.now() + timedelta(hours=48)).date()
                
                if delivery_date < min_date:
                    # Return error message
                    return render(request, 'partials/checkout_step_1.html', {
                        'error_message': f'Data de livrare trebuie să fie cel puțin 48 de ore în viitor. Data minimă permisă: {min_date.strftime("%d/%m/%Y")}'
                    })
            except ValueError:
                return render(request, 'partials/checkout_step_1.html', {
                    'error_message': 'Data de livrare nu este validă.'
                })
        
        # Calculate delivery fee based on delivery type
        delivery_fee = 0 if delivery_type == 'pickup' else 29
        
        # Calculate total
        user = request.user if request.user.is_authenticated else None
        session_id = request.session.session_key or request.session.create()
        cart_items = get_cart_items(request)
        subtotal = sum(item.total_price() for item in cart_items)
        total_price = subtotal + delivery_fee

        # Create the Order with new fields
        order = Order.objects.create(
            user=user,
            session_id=None if user else session_id,
            full_name=request.POST.get("full_name"),
            email=request.POST.get("email"),
            address=request.POST.get("address") if delivery_type == 'delivery' else None,
            phone_number=request.POST.get("phone_number"),
            city=request.POST.get("city") if delivery_type == 'delivery' else None,
            zip_code=request.POST.get("zip_code") if delivery_type == 'delivery' else None,
            total_price=subtotal,
            delivery_fee=delivery_fee,
            payment_method=payment_method,
            payment_status=False if payment_method == 'cash' else False,  # Cash on delivery is not paid yet, card payments will be updated after successful payment
            delivery_type=delivery_type,
            desired_delivery_date=request.POST.get("desired_delivery_date"),
            delivery_time_slot=request.POST.get("delivery_time_slot"),
            delivery_notes=request.POST.get("delivery_notes", "")
        )

        # Save CartItems to OrderItems
        for item in cart_items:
            OrderItem.objects.create(order=order, product=item.product, quantity=item.quantity, price=item.product.price)

        # Clear the cart
        cart_items.delete()

        # Handle payment method logic
        if payment_method == 'cash':
            # For cash on delivery, go directly to success page
            return render(request, 'partials/order_success.html')
        else:
            # For card payment, continue to step 2
            request.session["order_id"] = order.id
            request.session["total_price"] = float(total_price)

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
            return JsonResponse({"success": False, "message": "Plata a eșuat."})

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
                return JsonResponse({"success": False, "message": "Order not found."})

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



def generate_bouquet_image(shape_id, wrapping_id=None, flowers_data=None, greenery_data=None, wrapping_color_hex="#FFFFFF"):
  
    try:
        shape = BouquetShape.objects.get(id=shape_id)
        if wrapping_id is None:
            wrapping = WrappingPaper.objects.first()  
        else:
            wrapping = WrappingPaper.objects.get(id=wrapping_id)
    except (BouquetShape.DoesNotExist, WrappingPaper.DoesNotExist):
        return None

    canvas_size = (500, 500)
    center = (canvas_size[0] // 2, canvas_size[1] // 2)
    base_radius = 120  # inner circle radius
    radius_step = 50   # distance between circles

    # Create canvas
    image = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)

    all_items = []
    
    total_flowers = 0
    for flower_data in flowers_data:
        try:
            flower = Flower.objects.get(id=flower_data["id"])
            count = int(flower_data["count"])
            total_flowers += count
            for _ in range(count):
                all_items.append((flower, "flower"))
        except (Flower.DoesNotExist, KeyError, ValueError):
            continue

    # Calculate greenery quantity: 20% of total flowers, minimum 1 of EACH type if greenery is selected
    greenery_quantity_per_type = 0
    if greenery_data and total_flowers > 0:
        greenery_quantity_per_type = max(1, int(total_flowers * 0.2))  # 20% of flowers, minimum 1
    
    # Add greenery (if provided and quantity calculated)
    if greenery_data and greenery_quantity_per_type > 0:
        greenery_types = []
        for green_data in greenery_data:
            try:
                green = Greenery.objects.get(id=green_data["id"])
                greenery_types.append(green)
            except (Greenery.DoesNotExist, KeyError, ValueError):
                continue
        
        # Add greenery items - ensure at least 1 of each type
        for green in greenery_types:
            # Add the calculated quantity for each greenery type
            for _ in range(greenery_quantity_per_type):
                all_items.append((green, "greenery"))

    num_items = len(all_items)
    print(f"Generated {num_items} items for image")
    if num_items == 0:
        print("No items to generate image")
        return None

    item_positions = []
    
    if num_items <= 5: 
        base_size = 250
        min_size = 120
        spiral_tightness = 0.8
    elif num_items <= 30:
        base_size = 200
        min_size = 100
        spiral_tightness = 1.0
    else:
        base_size = 180
        min_size = 90
        spiral_tightness = 1.2
    
    # Calculate adaptive spacing
    spacing_factor = max(0.6, min(1.5, 20 / num_items)) 
    
    for i, (item, item_type) in enumerate(all_items):
        distance_from_center = i / num_items
        size_factor = 1.2 - (distance_from_center * 0.3)  
        size = max(min_size, int(base_size * size_factor))
        
        # Spiral positioning
        if i == 0:
            
            x, y = center[0], center[1]
        else:
            
            angle = i * 137.5 * (math.pi / 180) * spiral_tightness  
            radius = (i ** 0.5) * spacing_factor * 25  
            
            x = int(center[0] + radius * math.cos(angle))
            y = int(center[1] + radius * math.sin(angle))
            
            max_offset = 180
            x = max(center[0] - max_offset, min(center[0] + max_offset, x))
            y = max(center[1] - max_offset, min(center[1] + max_offset, y))
        
        item_positions.append((y, x, item, size, item_type))

    # Sort positions and draw items
    item_positions.sort(key=lambda tup: (tup[0], tup[1]))

    for y, x, obj, size, obj_type in item_positions:
        try:
            if obj_type == "flower":
                flower_img = Image.open(obj.image.path).convert("RGBA")
                flower_img = flower_img.resize((size, size), Image.LANCZOS)
                bottom_center_x = center[0]
                bottom_center_y = canvas_size[1]
                dx = bottom_center_x - x
                dy = bottom_center_y - y
                angle_rad = math.atan2(dx, dy)
                angle_deg = math.degrees(angle_rad)
                rotated_img = flower_img.rotate(angle_deg, expand=True)
            else:  
                greenery_img = Image.open(obj.image.path).convert("RGBA")
                greenery_img = greenery_img.resize((size, size), Image.LANCZOS)
                bottom_center_x = center[0]
                bottom_center_y = canvas_size[1]
                dx = bottom_center_x - x
                dy = bottom_center_y - y
                angle_rad = math.atan2(dx, dy)
                angle_deg = math.degrees(angle_rad)
                rotated_img = greenery_img.rotate(angle_deg, expand=True)

            rx, ry = rotated_img.size
            image.paste(rotated_img, (x - rx // 2, y - ry // 2), rotated_img)
        except Exception as e:
            print(f"Error processing flower/greenery: {e}")
            continue

    return image

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
                "error": "Te rugăm să selectezi forma buchetului."
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
                "error": "Te rugăm să selectezi cel puțin o floare."
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
        
        # Debug logging
        print(f"Save custom bouquet request data: {dict(data)}")

        try:
            shape = BouquetShape.objects.get(id=data.get("shape"))
        except BouquetShape.DoesNotExist:
            print(f"Invalid shape ID: {data.get('shape')}")
            return JsonResponse({"error": "Invalid shape selected."}, status=400)

        # Get wrapping paper (not variant)
        wrapping_id = data.get("wrapping")
        wrapping = None
        if wrapping_id:
            try:
                wrapping = WrappingPaper.objects.get(id=wrapping_id)
            except WrappingPaper.DoesNotExist:
                return JsonResponse({"error": "Hârtia selectată nu este validă."}, status=400)
        else:
            # Use first available wrapping if none selected
            wrapping = WrappingPaper.objects.first()
            if not wrapping:
                return JsonResponse({"error": "Nu există hârtie de ambalaj disponibilă."}, status=400)

        # Greenery
        greenery_ids = data.getlist("greens")
        greenery_list = Greenery.objects.filter(id__in=greenery_ids)

        # Flowers
        flower_quantities = {}
        for flower in Flower.objects.all():
            qty = int(data.get(f"flower_{flower.id}", 0))
            if qty > 0:
                flower_quantities[flower.id] = qty
        
        print(f"Flower quantities: {flower_quantities}")
        print(f"Greenery list: {list(greenery_list.values_list('id', flat=True))}")

        # Calculate total price
        total_price = float(data.get("total_price", 0))

        # Generate the bouquet preview image
        import os
        from django.conf import settings
        
        # Get wrapping color
        wrapping_color_name = data.get("wrapping_color_name", "")
        color_hex = "#FFFFFF"  # Default white
        
        # Try to get the color from WrappingColor if name is provided
        if wrapping_color_name:
            try:
                wrapping_color = WrappingColor.objects.get(name=wrapping_color_name)
                color_hex = wrapping_color.hex
            except WrappingColor.DoesNotExist:
                pass  # Use default white

        # Convert flower quantities to flowers_data format
        flowers_data = []
        for flower_id, quantity in flower_quantities.items():
            flowers_data.append({"id": flower_id, "count": quantity})

        # Convert greenery to greenery_data format (same as preview)
        greenery_data = []
        for green in greenery_list:
            greenery_data.append({"id": green.id, "count": 1})  # Use same format as preview

        print(f"Calling generate_bouquet_image with:")
        print(f"  shape_id: {shape.id}")
        print(f"  wrapping_id: {wrapping.id}")
        print(f"  flowers_data: {flowers_data}")
        print(f"  greenery_data: {greenery_data}")
        print(f"  color_hex: {color_hex}")

        # Generate the image using the reusable function
        image = generate_bouquet_image(
            shape_id=shape.id,
            wrapping_id=wrapping.id,
            flowers_data=flowers_data,
            greenery_data=greenery_data,
            wrapping_color_hex=color_hex
        )

        if image is None:
            return JsonResponse({"error": "Nu ai selectat flori."}, status=400)

        # Save the generated image
        media_root = settings.MEDIA_ROOT
        custom_images_dir = os.path.join(media_root, 'custom_bouquets')
        os.makedirs(custom_images_dir, exist_ok=True)
        
        image_filename = f"custom_bouquet_{int(timezone.now().timestamp())}.png"
        image_path = os.path.join(custom_images_dir, image_filename)
        image.save(image_path, "PNG")
        
        # Relative path for database
        relative_image_path = f"custom_bouquets/{image_filename}"

        # Create the bouquet
        custom_bouquet = CustomBouquet.objects.create(
            user=request.user if request.user.is_authenticated else None,
            shape=shape,
            wrapping=wrapping,
        )
        custom_bouquet.greenery.set(greenery_list)

        # Add flowers
        for flower_id, quantity in flower_quantities.items():
            BouquetFlower.objects.create(
                bouquet=custom_bouquet,
                flower_id=flower_id,
                quantity=quantity
            )

        # Create the product with generated image
        custom_product = Product.objects.create(
            name=f"Buchet personalizat #{custom_bouquet.id}",
            price=total_price,
            is_custom=True,
            image=relative_image_path,
            category='CustomBouquet',
            stock=0  # Like buchete/aranjamente, no stock limit
        )

        # Associate product with bouquet
        custom_bouquet.product = custom_product
        custom_bouquet.save()

        # Add to cart
        if request.user.is_authenticated:
            cart_item, _ = CartItem.objects.get_or_create(user=request.user, product=custom_product)
        else:
            session_id = get_or_create_session_id(request)
            cart_item, _ = CartItem.objects.get_or_create(session_id=session_id, product=custom_product)

        cart_item.quantity = 1
        cart_item.save()

        # Redirect to cart
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

    # More lenient validation - allow preview even without shape/wrapping
    if not shape_id:
        return JsonResponse({"error": "Forma buchetului este invalidă."}, status=400)

    flowers_data = data.get("flowers", [])
    greens = data.get("greens", [])  # Frontend sends 'greens' not 'greenery'
    wrapping_color_hex = data.get("wrapping_color", "#FFFFFF")

    # Debug logging
    print(f"Preview request - shape_id: {shape_id}, wrapping_id: {wrapping_id}")
    print(f"Flowers: {flowers_data}, Greens: {greens}")

    # Convert greens array to greenery_data format
    greenery_data = []
    for green_id in greens:
        greenery_data.append({"id": int(green_id), "count": 1})

    # Check if we have any items to generate
    if not flowers_data and not greens:
        # Return a simple placeholder image with just the wrapping
        try:
            shape = BouquetShape.objects.get(id=shape_id)
            # Use first available wrapping if none selected
            if wrapping_id is None:
                wrapping = WrappingPaper.objects.first()
            else:
                wrapping = WrappingPaper.objects.get(id=wrapping_id)
        except (BouquetShape.DoesNotExist, WrappingPaper.DoesNotExist):
            return JsonResponse({"error": "Forma sau ambalajul nu există."}, status=400)

        canvas_size = (500, 500)
        center = (canvas_size[0] // 2, canvas_size[1] // 2)
        base_radius = 120

        # Create canvas with just wrapping
        image = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
        draw = ImageDraw.Draw(image)

        # Draw wrapping circle
        draw.ellipse([
            (center[0] - base_radius, center[1] - base_radius),
            (center[0] + base_radius, center[1] + base_radius)
        ], fill=wrapping_color_hex)

        # Return the placeholder image
        response = HttpResponse(content_type="image/png")
        image.save(response, "PNG")
        return response

    # Generate the image using the reusable function
    image = generate_bouquet_image(
        shape_id=int(shape_id),
        wrapping_id=int(wrapping_id) if wrapping_id else None,  # Pass None if not selected
        flowers_data=flowers_data,
        greenery_data=greenery_data,
        wrapping_color_hex=wrapping_color_hex
    )

    if image is None:
        print("Failed to generate image - no items found")
        return HttpResponse(status=400)

    # Return the image
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
    subject = f"Confirmare comandă - #{order.id}"
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
    email.attach(f"factura_{order.id}.pdf", pdf_file.getvalue(), 'application/pdf')
    email.send()


from django.utils import timezone

def contact_view(request):
    form = ContactForm(request.POST or None)

    if form.is_valid():
        contact_message = form.save(commit=False)
        contact_message.submitted_at = timezone.now()
        contact_message.save()

        # Email către administrator
        subject = f"Mesaj de contact de la {contact_message.name}"
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

def order_success(request):
    return render(request, 'partials/order_success.html')

def update_order_summary(request):
    """Update order summary based on delivery type"""
    if request.method == 'POST':
        delivery_type = request.POST.get('delivery_type', 'delivery')
        delivery_fee = 0 if delivery_type == 'pickup' else 29
        
        user = request.user if request.user.is_authenticated else None
        session_id = request.session.session_key or request.session.create()
        cart_items = CartItem.objects.filter(user=user) if user else CartItem.objects.filter(session_id=session_id)
        
        subtotal = sum(item.total_price() for item in cart_items)
        total_price = subtotal + delivery_fee
        
        return render(request, 'partials/order_summary.html', {
            'cart_items': cart_items,
            'subtotal': subtotal,
            'delivery_fee': delivery_fee,
            'total_price': total_price,
            'delivery_type': delivery_type
        })
    
    return JsonResponse({'error': 'Invalid request'})


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

