from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse, HttpResponseBadRequest
from .models import ContactMessage, Product, CartItem, Order, Payment, OrderItem
from .models import BouquetShape, Flower, Greenery, WrappingPaper, CustomBouquet, BouquetFlower
from django.contrib.auth.decorators import login_required
from django.template.loader import render_to_string
from django.http import HttpResponse
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.contrib.auth import authenticate, login, logout
from django.contrib import messages
from django.core.paginator import Paginator
from .forms import ContactForm, UserForm

import stripe
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods, require_POST
from django.templatetags.static import static  # Import the static function
from django.shortcuts import render
from .models import Product
from datetime import timedelta
from django.utils import timezone
from django.core.mail import send_mail
from django.utils.html import strip_tags
from django.core.mail import EmailMessage
from weasyprint import HTML
from io import BytesIO
from django.db.models import Q

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

    if not created:
        cart_item.quantity += 1
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
    
    cart_product_ids = get_cart_items(request).values_list('product_id', flat=True)
    context = {
        'products': products,
        'category': category,
        'sort_order': sort_order,
        'min_price': min_price,
        'max_price': max_price,
        'cart_product_ids': cart_product_ids
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
    #form = UserForm(instance=request.user)
    user = request.user
    if request.method == 'POST':
        form = UserForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            return redirect('profile')  # or show a success message
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

    #return redirect('checkout_step_1')

def custom_bouquet_builder(request):
    shape = BouquetShape.objects.all()
    wrapping = WrappingPaper.objects.all()
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

        # Render the summary
        return render(request, "custom_bouquet_summary.html", {
            "shape": shape,
            "wrapping": wrapping_list,
            "greens": greenery_list,
            "flower_summary": flower_summary,
            "total_price": total_price,
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

        # Wrapping
        wrapping_id = data.get("wrapping")
        wrapping = WrappingPaper.objects.filter(id=wrapping_id).first()

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

        # Save the bouquet
        custom_bouquet = CustomBouquet.objects.create(
            user=request.user if request.user.is_authenticated else None,
            shape=shape,
            wrapping=wrapping,
        )
        custom_bouquet.greenery.set(greenery_list)

        # Add flowers with quantities
        for flower_id, quantity in flower_quantities.items():
            BouquetFlower.objects.create(
                bouquet=custom_bouquet,
                flower_id=flower_id,
                quantity=quantity
            )

        # Create a Product representing this custom bouquet
        custom_product = Product.objects.create(
            name=f"Buchet personalizat #{custom_bouquet.id}",
            price=total_price,
            is_custom=True,
            #custom_bouquet=custom_bouquet,
            image='Logo.png',
            category='CustomBouquet'  
        )

        # Link product to bouquet
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

        # Redirect to the cart page
        response = HttpResponse()
        response["HX-Redirect"] = "/cart/"
        return response

    return JsonResponse({"error": "Invalid request"}, status=400)


def auction_view(request):
    products = Product.objects.all()
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
        cart_item.product.price = product.get_auction_price()
        cart_item.product.bid_submited = True
        cart_item.product.save()

    # Always refresh the product list (optional), and cart count
    # if request.headers.get("HX-Request"):
    #     cart_count = get_cart_items(request).count()
    #     cart_html = render_to_string("partials/cart_count.html", {"cart_count": cart_count}, request=request)

    #     # Return a response that updates the cart count (via hx-swap-oob)
    #     response = HttpResponse(cart_html)
    #     response["HX-Trigger"] = "refreshProductList"
    #     return response

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

        # Send email to business
        subject = f"New Contact Message from {contact_message.name}"
        message = contact_message.message
        from_email = contact_message.email
        to_email = [settings.DEFAULT_FROM_EMAIL]

        send_mail(
            subject,
            message,
            from_email,
            to_email,
            fail_silently=False,
        )

        return redirect('contact_success')

    return render(request, 'contact.html', {'form': form})


def contact_success(request):

    return render(request, 'contact_success.html')