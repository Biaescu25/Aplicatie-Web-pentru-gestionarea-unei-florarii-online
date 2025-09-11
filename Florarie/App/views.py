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
from django.db.models import Sum, Count, F
from django.db import transaction
from datetime import datetime
from django.contrib.auth import authenticate

from .models import (
    Product, CartItem, Order, Payment, OrderItem,
    BouquetShape, Flower, Greenery, WrappingPaper, WrappingColor, CustomBouquet, BouquetFlower, VisitorLog, WrappingPaperColor
)
from .forms import ContactForm, UserForm

import matplotlib
matplotlib.use('Agg')  
import stripe
stripe.api_key = settings.STRIPE_SECRET_KEY  # Stripe API key

import json
from PIL import Image, ImageDraw
from django.conf import settings
import math

import os


def home(request):
    all_products = Product.objects.all().order_by('-number_of_purcheses')
    top_products = []
    for product in all_products:
        if not product.is_custom:
            top_products.append(product)
        if len(top_products) == 3:
            break

    cart_product_ids = get_cart_items(request).values_list('product_id', flat=True)

    return render(request, "home.html", {'products': top_products, 'cart_product_ids': cart_product_ids})

def cart_view(request):
    if request.user.is_authenticated:
        base_qs = CartItem.objects.filter(user=request.user)
    else:
        session_id = request.session.session_key
        if not session_id:
            request.session.create()
            session_id = request.session.session_key
        base_qs = CartItem.objects.filter(session_id=session_id)

    expired = base_qs.filter(reserved_until__isnull=False, reserved_until__lte=timezone.now())
    if expired.exists():
        for item in expired:
            remove_from_cart(request, item.product_id)
        expired.delete()

    cart_items = base_qs  
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

    # Ignora stock check pentru aceste categorii
    stock_managed_product = product.category not in ['buchete', 'aranjamente', 'CustomBouquet']

    # Pentru produsele gestionate de stoc
    if stock_managed_product:
        with transaction.atomic():
            # Selecteaza produsul din baza de date si blocheaza randul
            product = Product.objects.select_for_update().get(id=product_id)
            
            # Calculeaza cantitatea totala rezervata (inclusiv in cosul utilizatorului curent)
            total_reserved_quantity = CartItem.objects.filter(
                product=product, 
                reserved_until__gt=timezone.now()
            ).aggregate(
                total=Sum('quantity') #Calculeaza suma cosurilor active
            )['total'] or 0
            
            # Verifica daca adaugarea unuia in plus ar depasi stocul
            if total_reserved_quantity >= product.stock:
                error_message = f"Ne pare rău, produsul '{product.name}' nu mai este disponibil. Toate unitățile sunt momentan rezervate în coșuri."
                
                # Returneaza mesaj de eroare pentru toate tipurile de cereri
                return HttpResponse(error_message, status=400)

   #Adauga sau actualizeaza elementul in cos
    if request.user.is_authenticated:
        cart_item, created = CartItem.objects.get_or_create(user=request.user, product=product)
    else:
        session_id = get_or_create_session_id(request)
        cart_item, created = CartItem.objects.get_or_create(session_id=session_id, product=product)

    # Daca elementul este nou, seteaza cantitatea la 1 si timpul de rezervare
    if created:
        cart_item.quantity = 1
        if stock_managed_product:
            cart_item.reserved_until = timezone.now() + timedelta(minutes=15)
            cart_item.save()
    else:
        # Daca elementul exista deja,  incrementezi cantitatea
        if product.category in ['buchete', 'aranjamente']:
            # Permite doar 10
                if cart_item.quantity < 10:
                    cart_item.quantity += 1
                cart_item.save()
        else:
                # Pentru produsele gestionate de stoc
                # Calculeaza cantitatea totala rezervata din cos
                total_reserved = CartItem.objects.filter(
                    product=product, 
                    reserved_until__gt=timezone.now()
                ).aggregate(
                    total=Sum('quantity') 
                )['total'] or 0

                # Verific stocul
                if total_reserved < product.stock:
                    cart_item.quantity += 1
                    # Recalculeaza rezervarea
                    cart_item.reserved_until = timezone.now() + timedelta(minutes=15)
                    cart_item.save()
                else:
                    error_message = f"Ne pare rău, nu se poate adăuga mai mult din produsul '{product.name}'. Toate unitățile sunt momentan rezervate."
                    return HttpResponse(error_message, status=400)

    # Daca requestul e htmx
    if request.headers.get("HX-Request"):
        cart_count = get_cart_items(request).count()
        # Randeaza partialul pentru numarul de produse din cos
        html = render_to_string("partials/cart_count.html", {"cart_count": cart_count})
        
        response = HttpResponse(html)

        # Adauga un trigger pentru a actualiza dinamic
        response["HX-Trigger"] = json.dumps({
            "cartUpdated": {"productId": product_id}
        })
        
        return response
    
    return redirect("cart")

def remove_from_cart(request, product_id):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        # Daca e un buchet personalizat
        if cart_item.product.is_custom and cart_item.product.category == 'CustomBouquet':
            # Sterge buchetul personalizat si produsul asociat
            try:
                custom_bouquet = CustomBouquet.objects.get(product=cart_item.product)
                # Sterge mai intai produsul 
                cart_item.product.delete()
                # Sterge buchetul personalizat
                custom_bouquet.delete()
            except CustomBouquet.DoesNotExist:
                # Daca nu exista un buchet personalizat, sterge doar produsul din cos
                cart_item.product.delete()
        else:
            # Pentru produsele obisnuite, gestioneaza logica 
            if cart_item.product.bid_submited:
                # Daca produsul este la licitatie, seteaza bid_submited la False
                cart_item.product.bid_submited = False
                cart_item.product.price = cart_item.product.before_auction_price  # Reseteaza la pretul original
                cart_item.product.save() 
            cart_item.delete()

    if request.user.is_authenticated:
        cart_items = CartItem.objects.filter(user=request.user)
    else:
        session_id = request.session.session_key
        cart_items = CartItem.objects.filter(session_id=session_id)

    total_price = sum(item.total_price() for item in cart_items)

    if request.headers.get("HX-Request"):  # Daca e un request HTMX
        if not cart_items:  # Daca cosul e gol, refresh-uieste intreaga pagina
            response = HttpResponse("")
            response["HX-Redirect"] = "/cart/"  # Redirectioneaza catre pagina cosului
            return response

        # Daca cosul nu e gol, actualizeaza atat produsele din cos cat si pretul total
        total_price_html = render_to_string("partials/total_price.html", {"total_price": total_price}, request=request)
    
        cart_count = get_cart_items(request).count()
        cart_html = render_to_string("partials/cart_count.html", {"cart_count": cart_count}, request=request)
 
        response = HttpResponse(cart_html)

        response["HX-Trigger"] = "updateTotalPrice"  # Trigger la total price script
        return response


    # Pentru requesturile normale, redirectioneaza catre pagina cosului
    return redirect("cart")

def update_cart(request, product_id, quantity):
    cart_item = CartItem.objects.filter(product_id=product_id).first()
    if cart_item:
        # Pentru buchete, aranjamente, si custom bouquets pana la 10
        if cart_item.product.category in ['buchete', 'aranjamente']:
            cart_item.quantity = min(quantity, 10)
            cart_item.save()
        else:
            # Pentru celelalte produse, verifica impotriva cantitatii totale rezervate
            # Calculeaza current total reserved quantity
            total_reserved = CartItem.objects.filter(
                product=cart_item.product, 
                reserved_until__gt=timezone.now()
            ).aggregate(
                total=Sum('quantity') 
            )['total'] or 0

            # Calculeaza cat de mult contribuie acest cart item in prezent
            current_contribution = cart_item.quantity

            # Calculeaza cantitatea disponibila (total rezervat minus contributia curenta)
            available_quantity = cart_item.product.stock - (total_reserved - current_contribution)

            # Cantitatea rezervata in cos este min dintre cantitatea ceruta si cantitatea disponibila
            new_quantity = min(quantity, available_quantity)
            
            if new_quantity < quantity:
                # Returneaza mesaj de eroare pentru toate tipurile de cereri
                error_message = f"Nu se poate seta cantitatea la {quantity} pentru '{cart_item.product.name}'. Cantitatea maximă disponibilă este {new_quantity}."
                return JsonResponse({"success": False, "error": error_message}, status=400)
            
            cart_item.quantity = new_quantity
            cart_item.save()
    return JsonResponse({"success": True, "total_price": cart_item.total_price()})

def increment_quantity(request, product_id):
    if request.user.is_authenticated:
        cart_item = CartItem.objects.filter(product_id=product_id, user=request.user).first()
    else:
        session_id = request.session.session_key
        cart_item = CartItem.objects.filter(product_id=product_id, session_id=session_id).first()
    
    if cart_item:
        if cart_item.product.category in ['buchete', 'aranjamente', 'CustomBouquet']:
            if cart_item.quantity < 10:
                cart_item.quantity += 1
                cart_item.save()
            else:
                cart_item.error_message = f"Nu se poate adăuga mai mult din '{cart_item.product.name}'. Maxim 10 unități."
        else:
            total_reserved = CartItem.objects.filter(
                product=cart_item.product, 
                reserved_until__gt=timezone.now()
            ).aggregate(total=Sum('quantity'))['total'] or 0
            
            if total_reserved < cart_item.product.stock:
                cart_item.quantity += 1
                cart_item.save()
            else:
                cart_item.error_message = f"Nu se poate adăuga mai mult din '{cart_item.product.name}'. Toate unitățile sunt rezervate."

    cart_items = get_cart_items(request)

    if request.headers.get('HX-Request'):
        item_html = render_to_string("partials/cart_item.html", {"cart_item": cart_item}, request=request)
        response = HttpResponse(item_html)
        response["HX-Trigger"] = "updateTotalPrice"
        return response

    return redirect("cart")


def decrement_quantity(request, product_id):
    if request.user.is_authenticated:
        cart_item = CartItem.objects.filter(product_id=product_id, user=request.user).first()
    else:
        session_id = request.session.session_key
        cart_item = CartItem.objects.filter(product_id=product_id, session_id=session_id).first()
    
    if cart_item and cart_item.quantity > 1:
        cart_item.quantity -= 1
        cart_item.save()

    elif cart_item and cart_item.quantity == 1:
        return remove_from_cart(request, product_id)

    cart_items = get_cart_items(request)
    total_price = sum(item.total_price() for item in cart_items)

    if request.headers.get('HX-Request'):
        item_html = render_to_string("partials/cart_item.html", {"cart_item": cart_item}, request=request) if cart_item else "" # generare html pentru item cos
        total_price_html = render_to_string("partials/total_price.html", {"total_price": total_price}, request=request)  #generare html pentru totalul cosului
        
        response = HttpResponse(item_html)
        response["HX-Trigger"] = "updateTotalPrice"  # Trigger total pentru actualizarea pretului
        return response


    return redirect("cart")


def products_by_category(request: HttpRequest, category):
    products = Product.objects.filter(category=category)

    # Determina pretul minim si maxim
    smallest_price = products.order_by('price').first().price if products.exists() else 0
    largest_price = products.order_by('-price').first().price if products.exists() else 0

    # Filtrare dupa intervalul de pret
    min_price = request.GET.get('min_price')
    max_price = request.GET.get('max_price')
    if min_price and max_price:
        products = products.filter(price__gte=min_price, price__lte=max_price)

    # Aplicare sortare
    sort_order = request.GET.get('sort', 'asc')
    if sort_order == 'asc':
        products = products.order_by('price')
    elif sort_order == 'desc':
        products = products.order_by('-price')

    # Determina daca produsul este nou
    now = timezone.now()
    new_threshold = now - timedelta(hours=48)
    
    # Ia id-ul produselor din cosul utilizatorului
    cart_product_ids = list(get_cart_items(request).values_list('product_id', flat=True))

    # Adauga proprietatea is_new pentru produse
    products = list(products)
    for product in products:
        product.is_new = product.created_at >= new_threshold

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
                item.session_id = None  # Sterge asocierea cu sesiunea
                item.save()


def register(request):
    if request.method == "POST":
        username = request.POST["username"]
        email = request.POST["email"]
        password1 = request.POST["password1"]
        password2 = request.POST["password2"]

        #RESTRICITILE DE PAROLA
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


        # Verifica daca parolele se potrivesc
        if password1 != password2:
            errors.append("Parolele nu se potrivesc.")

        # Verifica daca username-ul exista deja
        if User.objects.filter(username=username).exists():
            errors.append("Username este deja folosit.")

        # Verifica daca email-ul este deja folosit
        if User.objects.filter(email=email).exists():
            errors.append("Email este deja folosit.")

        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect("register")

        # Creaza utilizatorul
        user = User.objects.create_user(username=username, email=email, password=password1)
        user.save()

        # Logheaza utilizatorul automat dupa inregistrare
        user = authenticate(request, username=username, password=password1)
        login(request, user, backend='django.contrib.auth.backends.ModelBackend')

        return redirect("home")

    return render(request, "register.html")

def user_login(request):
    if request.method == "POST":
        username = request.POST["username"]
        password = request.POST["password"]

        user = authenticate(request, username=username, password=password)

        if user is not None:
            login(request, user)
            return redirect("home") 
        else:
            messages.error(request, "Credențiale invalide.")
            return redirect("login")

    return render(request, "login.html")

def user_logout(request):
    if request.method == 'POST':
        logout(request)
        return redirect('home')
    return redirect('logout_confirm')

@login_required
def logout_confirm(request):
    if request.headers.get('HX-Request'):
        return render(request, 'partials/logout_confirm.html')
    return redirect('home')

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


stripe.api_key = settings.STRIPE_SECRET_KEY  #Stripe API key

@login_required
def checkout(request):
   # Preia elementele din cos
    user = request.user if request.user.is_authenticated else None
    session_id = request.session.session_key or request.session.create()
    cart_items = CartItem.objects.filter(user=user) if user else CartItem.objects.filter(session_id=session_id)
   #
    subtotal = sum(item.total_price() for item in cart_items)
    delivery_fee = 29  # lei default
    total_price = subtotal + delivery_fee

    if request.method == "POST":
        payment_method = request.POST["payment_method"]

        # Creeaza comanda
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

        # Salveaza elementele din cos ca elemente de comanda
        for item in cart_items:
            price = item.product.price if item.product.price is not None else 0  
            OrderItem.objects.create(order=order, product=item.product, quantity=item.quantity, price=price)

        # Goleste cosul
        cart_items.delete()

        if payment_method == "card":
            request.session["order_id"] = order.id  
            return redirect("process_payment")  

        return redirect("order_success")  
    
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
       # Preia datele din formular
        delivery_type = request.POST.get('delivery_type', 'delivery')
        payment_method = request.POST.get('payment_method')
        desired_delivery_date = request.POST.get("desired_delivery_date")

        # Valideaza data de livrare
        if desired_delivery_date:
            from datetime import datetime, timedelta
            try:
                delivery_date = datetime.strptime(desired_delivery_date, '%Y-%m-%d').date()
                min_date = (datetime.now() + timedelta(hours=48)).date()
                
                if delivery_date < min_date:
                    return render(request, 'partials/checkout_step_1.html', {
                        'error_message': f'Data de livrare trebuie să fie cel puțin 48 de ore în viitor. Data minimă permisă: {min_date.strftime("%d/%m/%Y")}'
                    })
            except ValueError:
                return render(request, 'partials/checkout_step_1.html', {
                    'error_message': 'Data de livrare nu este validă.'
                })

        # Calculeaza taxa de livrare in functie de tipul de livrare
        delivery_fee = 0 if delivery_type == 'pickup' else 29

        # Calculeaza totalul
        user = request.user if request.user.is_authenticated else None
        session_id = request.session.session_key or request.session.create()
        cart_items = get_cart_items(request)
        subtotal = sum(item.total_price() for item in cart_items)
        total_price = subtotal + delivery_fee

        # Creaza comanda
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
            payment_status=False if payment_method == 'cash' else False, 
            delivery_type=delivery_type,
            desired_delivery_date=request.POST.get("desired_delivery_date"),
            delivery_time_slot=request.POST.get("delivery_time_slot"),
            delivery_notes=request.POST.get("delivery_notes", "")
        )

        # Salveaza CartItems ca OrderItems
        for item in cart_items:
            OrderItem.objects.create(order=order, product=item.product, quantity=item.quantity, price=item.product.price)

        # Finalizeaza comanda
        if payment_method == 'cash':
            try:
               finish_order(request, order)
               return render(request, 'partials/order_success.html')
            except Order.DoesNotExist:
                return JsonResponse({"success": False, "message": "Comanda nu a fost găsită."})

        else:
            # Pentru plata cu cardul, continua la pasul 2
            # salvez order_id si total_price in sesiune
            request.session["order_id"] = order.id
            request.session["total_price"] = float(total_price)

        return render(request, 'checkout_step_2.html', {
            'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
        })


    return redirect('checkout_step_1')

def finish_order(request, order):
    order.payment_status = True
    order.save()

    send_order_email(order, request.user, email_destination=order.email)

    # Actualizeaza stocul produselor
    for item in order.items.all():
        if item.product.stock is not None and item.product.stock > 0:
            item.product.stock = max(0, item.product.stock - item.quantity)
            item.product.save()

    # Sterge articolele din cos
    user = request.user if request.user.is_authenticated else None
    session_id = request.session.session_key
    cart_items = CartItem.objects.filter(user=user) if user else CartItem.objects.filter(session_id=session_id)

    # Actualizeaza numarul de vanzari pentru fiecare produs
    for item in cart_items:
        item.product.number_of_purcheses += item.quantity
        item.product.save()

    cart_items.delete()

@csrf_exempt
def checkout_step_3(request): #finalizam plata cu cardul folosind stripe
    if request.method == 'POST':
        token = request.POST.get('stripeToken') #tokenul de card trimis de stripe
        amount = float(request.session.get("total_price", 0))

        if not token or amount == 0:
            return JsonResponse({"success": False, "message": "Plata a eșuat."})

        try:
            #creeaza tranzactia Stripe
            charge = stripe.Charge.create(
                amount=int(amount * 100),  #convertim in cents
                currency="ron",
                description="Order Payment",
                source=token,
            )

            # Salvez plata in DB
            Payment.objects.create(
                user=request.user if request.user.is_authenticated else None,
                amount=amount,
                transaction_id=charge.id,
                status="Completed"
            )

            # Finalizeaza comanda
            order_id = request.session.get("order_id")
            if not order_id:
                return JsonResponse({"success": False, "message": "Comanda nu a fost găsită."})
                
            try:
                order = Order.objects.get(id=order_id)
                finish_order(request, order)
            except Order.DoesNotExist:
                return JsonResponse({"success": False, "message": "Comanda nu a fost găsită."})

            if request.headers.get("HX-Request"):
                return render(request, 'checkout_step_3.html')
            else:
                return redirect("order_success")  # fallback daca nu e HTMX
        
        except stripe.error.CardError as e:
            return JsonResponse({"success": False, "message": f"Eroare card: {str(e)}"})
        except stripe.error.StripeError as e:
            return JsonResponse({"success": False, "message": f"Eroare Stripe: {str(e)}"})
        except Exception as e:
            return JsonResponse({"success": False, "message": f"Eroare neașteptată: {str(e)}"})

    return JsonResponse({"success": False, "message": "Cerere invalidă."})

def order_success(request):
    return render(request, 'partials/order_success.html')

def update_order_summary(request):
    # Actualizeaza sumarul comenzii
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
    
    return JsonResponse({'error': 'Invalid request'}, status=400)

#creare imagine buchet
def generate_bouquet_image(shape_id, wrapping_id=None, flowers_data=None, greenery_data=None, wrapping_color_hex="#FFFFFF"):
    # Configuram canvasul 
    canvas_size = (500, 500)
    center = (canvas_size[0] // 2, canvas_size[1] // 2)

    #cream canvasul
    image = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
    
    all_items = []

    # Calculam numarul total de flori
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

    # Calculam cantitatea de verdeata ca 20% din numarul total de flori, dar sa fie minim 1
    greenery_quantity_per_type = 0
    if greenery_data and total_flowers > 0:
        greenery_quantity_per_type = max(1, int(total_flowers * 0.2))  # 20% din flori

    # Adaugam verdeata (daca este furnizata si cantitatea este calculata)
    if greenery_data and greenery_quantity_per_type > 0:
        greenery_types = []
        for green_data in greenery_data:
            try:
                green = Greenery.objects.get(id=green_data["id"])
                greenery_types.append(green)
            except (Greenery.DoesNotExist, KeyError, ValueError):
                continue
        
        # Adaugam verdeata - asiguram cel putin 1 din fiecare tip
        for green in greenery_types:
            # Adaugam cantitatea calculata pentru fiecare tip de verdeata
            for _ in range(greenery_quantity_per_type):
                all_items.append((green, "greenery"))

    num_items = len(all_items)
    if num_items == 0:
        return None

    item_positions = []
    # cu cat sunt mai multe flori, scadem dimensiunea lor
    if num_items <= 5: 
        base_size = 250 #marimea de baza a unei flori/greenery
        min_size = 120 #marimea minima a unei flori/greenery
        spiral_tightness = 0.8 
    elif num_items <= 30:
        base_size = 200
        min_size = 100
        spiral_tightness = 1.0
    else:
        base_size = 180
        min_size = 90
        spiral_tightness = 1.2
    
    # Cu cat sunt mai multe flori, le distantam mai putin
    spacing_factor = max(0.6, min(1.5, 20 / num_items)) 
    
    for i, (item, item_type) in enumerate(all_items):
        dimension_fraction = i / num_items 
        size_factor = 1.2 - (dimension_fraction * 0.3)  #dim element scade odata cu distanta de centru
        size = max(min_size, int(base_size * size_factor)) #dim finala element

        # Calculam pozitia folosind un model spiralat
        if i == 0:
            
            x, y = center[0], center[1] #prima floare in centru
        else:
            
            angle = i * 137.5 * (math.pi / 180) * spiral_tightness #det unghi in spirala floare curenta
            radius = (i ** 0.5) * spacing_factor * 25  #det distanta fata de centru
            
            #transforma coordonate polare in coordonate carteziene
            x = int(center[0] + radius * math.cos(angle)) 
            y = int(center[1] + radius * math.sin(angle))
            
        #salvam pozitia si dim elementului
        item_positions.append((y, x, item, size, item_type))

    # Sortam pozitia
    item_positions.sort(key=lambda tup: (tup[0], tup[1]))

    for y, x, obj, size, obj_type in item_positions:
        try:
            if obj_type == "flower":
                flower_img = Image.open(obj.image.path).convert("RGBA")
                flower_img = flower_img.resize((size, size), Image.LANCZOS) #redim imaginea la dim calculata
                bottom_center_x = center[0] #retine coordonata X a bazei buchetului
                bottom_center_y = canvas_size[1] #retine coordonata Y a bazei buchetului
                dx = bottom_center_x - x #dif pe axa x intre floare si baza buchetului
                dy = bottom_center_y - y #dif pe axa y intre floare si baza buchetului
                angle_rad = math.atan2(dx, dy)
                angle_deg = math.degrees(angle_rad) #transforma unghiul in grade, necesar PIL
                rotated_img = flower_img.rotate(angle_deg, expand=True) 
            else:  
                greenery_img = Image.open(obj.image.path).convert("RGBA")
                greenery_img = greenery_img.resize((size, size), Image.LANCZOS) #redim imaginea la dim calculata
                bottom_center_x = center[0] #retine coordonata X a bazei buchetului
                bottom_center_y = canvas_size[1] #retine coordonata Y a bazei buchetului
                dx = bottom_center_x - x
                dy = bottom_center_y - y
                angle_rad = math.atan2(dx, dy)
                angle_deg = math.degrees(angle_rad)
                rotated_img = greenery_img.rotate(angle_deg, expand=True)

            rx, ry = rotated_img.size #dim imagine rotita
            image.paste(rotated_img, (x - rx // 2, y - ry // 2), rotated_img) #lipim imaginea pe canvas la pozitia calculata
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

#rezuamt buchet
def create_custom_bouquet(request):
    if request.method == "POST" and request.headers.get("HX-Request"):
        data = request.POST

        try:
            shape = BouquetShape.objects.get(id=data.get("shape"))
        except BouquetShape.DoesNotExist:
            # Afiseaza un mesaj de eroare
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
            # Afiseaza un mesaj de eroare
            return render(request, "custom_bouquet_summary.html", {
                "error": "Te rugăm să selectezi cel puțin o floare."
            })
        

        # Calculeaza pretul total
        total_price = wrapping_price + greenery_price + total_flower_price

        color_name = data.get("wrapping_color_name", "")

        # Randeaza rezumatul buchetului
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
#salveaza buchetul personalizat si creeaza produsul in DB
def save_custom_bouquet(request):
    if request.method == "POST":
        data = request.POST

        try:
            shape = BouquetShape.objects.get(id=data.get("shape"))
        except BouquetShape.DoesNotExist:
            return JsonResponse({"error": "Forma buchetului selectată nu este validă."}, status=400)

        # Wrapping
        wrapping_id = data.get("wrapping")
        wrapping = None
        if wrapping_id:
            try:
                wrapping = WrappingPaper.objects.get(id=wrapping_id)
            except WrappingPaper.DoesNotExist:
                return JsonResponse({"error": "Hârtia selectată nu este validă."}, status=400)
        else:
            # Folosește prima hârtie disponibilă dacă nu este selectată
            wrapping = WrappingPaper.objects.first()
            if not wrapping:
                return JsonResponse({"error": "Nu există hârtie de ambalaj disponibilă."}, status=400)

        # Preluam lista de verdeata
        greenery_ids = data.getlist("greens")
        greenery_list = Greenery.objects.filter(id__in=greenery_ids)

        # Preluam florile si cantitatile lor
        flower_quantities = {}
        for flower in Flower.objects.all():
            qty = int(data.get(f"flower_{flower.id}", 0))
            if qty > 0:
                flower_quantities[flower.id] = qty

        # Calculam pretul total 
        total_price = float(data.get("total_price", 0))

        # Preluam wrapping colorul
        wrapping_color_name = data.get("wrapping_color_name", "")
        color_hex = "#FFFFFF"  # Default white
        
        # Cautam culoarea in DB
        if wrapping_color_name:
            try:
                wrapping_color = WrappingColor.objects.get(name=wrapping_color_name)
                color_hex = wrapping_color.hex
            except WrappingColor.DoesNotExist:
                pass  

        # Convertim cantitatile florilor intr-o lista de dictionare
        flowers_data = []
        for flower_id, quantity in flower_quantities.items():
            flowers_data.append({"id": flower_id, "count": quantity})

        # Convertim verdeata intr-o lista de dictionare
        greenery_data = []
        for green in greenery_list:
            greenery_data.append({"id": green.id, "count": 1})  


        # Generam imaginea
        image = generate_bouquet_image(
            shape_id=shape.id,
            wrapping_id=wrapping.id,
            flowers_data=flowers_data,
            greenery_data=greenery_data,
            wrapping_color_hex=color_hex
        )

        if image is None:
            return JsonResponse({"error": "Nu ai selectat flori."}, status=400)

        # Salvam imaginea generata
        media_root = settings.MEDIA_ROOT
        custom_images_dir = os.path.join(media_root, 'custom_bouquets')
        os.makedirs(custom_images_dir, exist_ok=True)
        
        image_filename = f"custom_bouquet_{int(timezone.now().timestamp())}.png"
        image_path = os.path.join(custom_images_dir, image_filename)
        image.save(image_path, "PNG")

        # Path-ul pe care va fi salvata imaginea in DB
        relative_image_path = f"custom_bouquets/{image_filename}"

        # Cream buchetul personalizat
        custom_bouquet = CustomBouquet.objects.create(
            user=request.user if request.user.is_authenticated else None,
            shape=shape,
            wrapping=wrapping,
        )
        custom_bouquet.greenery.set(greenery_list)

        # Adaugam florile
        for flower_id, quantity in flower_quantities.items():
            BouquetFlower.objects.create(
                bouquet=custom_bouquet,
                flower_id=flower_id,
                quantity=quantity
            )

        # Cream produsul asociat buchetului personalizat
        custom_product = Product.objects.create(
            name=f"Buchet personalizat #{custom_bouquet.id}",
            price=total_price,
            is_custom=True,
            image=relative_image_path,
            category='CustomBouquet',
            stock=0  # Like buchete/aranjamente
        )

        # Asociem produsul cu buchetul
        custom_bouquet.product = custom_product
        custom_bouquet.save()

        # Adaugam produsul in cos
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

    data = json.loads(request.body) # Deserializam datele JSON, le convertim in dictionar

    shape_id = data.get("shape")
    wrapping_id = data.get("wrapping")

    # Validam forma buchetului
    if not shape_id:
        return JsonResponse({"error": "Forma buchetului este invalidă."}, status=400)

    flowers_data = data.get("flowers", [])
    greens = data.get("greens", [])  # Frontendul trimite 'greens' 
    wrapping_color_hex = data.get("wrapping_color", "#FFFFFF")

    # Convertim arrayul de 'greens' in formatul 'greenery_data'
    greenery_data = []
    for green_id in greens:
        greenery_data.append({"id": int(green_id), "count": 1})

    # Verificam daca avem elemente de generat
    if not flowers_data and not greens:
        # Returnam o imagine placeholder simpla cu doar ambalajul
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
        

        # cream imaginea de baza
        image = Image.new("RGBA", canvas_size, (255, 255, 255, 0))
       
        # Returneaza imaginea
        response = HttpResponse(content_type="image/png")
        image.save(response, "PNG")
        return response

    # Genereaza imaginea buchetului
    image = generate_bouquet_image(
        shape_id=int(shape_id),
        wrapping_id=int(wrapping_id) if wrapping_id else None,  
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

    # Stergem elementele din cos care au expirat si resetam bid_submited
    for product in products.filter(bid_submited=True):
        cart_items = CartItem.objects.filter(product=product)
        for cart_item in cart_items:
            if cart_item.is_expired():
                cart_item.delete()
                product.bid_submited = False
                product.save()

    # Filtram produsele active la licitatie care nu au expirat
    auction_products = []
    for p in products:
        if p.is_in_auction() and not p.is_auction_expired() and not p.bid_submited:
            auction_products.append(p)

    if request.headers.get("HX-Request"):
        return render(request, "partials/auction_list.html", {"products": auction_products})

    return render(request, "partials/auction.html", {"products": auction_products})

def auction_price_partial(request, pk):
    product = get_object_or_404(Product, pk=pk)
    part = request.GET.get("part")
    if part == "discount":
        try:
            _, _, discount_percent = product.get_auction_price()
        except Exception:
            discount_percent = 0

        if discount_percent and discount_percent > 0:
            badge_html = (
                '<div class="price-container absolute top-4 right-4 bg-red-600 text-white text-xl font-bold px-5 py-2 rounded-lg shadow-lg z-20 transform rotate-3">'
                f'-{int(round(discount_percent))}%'
                "</div>"
            )
            return HttpResponse(badge_html)
        return HttpResponse("")

    return render(request, "partials/auction_price.html", {"product": product})

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
        cart_item.reserved_until = timezone.now() + timedelta(minutes=15)
        cart_item.product.save()
        cart_item.save()

    if request.headers.get('HX-Request'):
        response = HttpResponse("")
        response['HX-Redirect'] = '/cart/'
        return response

    return redirect('cart')


def send_order_email(order, user, email_destination):
    subject = f"Confirmare comandă - #{order.id}"
    html_message = render_to_string("emails/order_confirmation_email.html", {"order": order, "user": user, "email_destination": email_destination})
    from_email = settings.DEFAULT_FROM_EMAIL
    to_email = [email_destination]

    # Generam factura PDF
    invoice_html = render_to_string('emails/invoice.html', {'order': order, 'user': user, 'email_destination': email_destination})
    pdf_file = BytesIO()
    HTML(string=invoice_html).write_pdf(pdf_file)

    # Email client
    email = EmailMessage(
        subject=subject,
        body=html_message,
        from_email=from_email,
        to=to_email,
    )
    email.content_subtype = "html"
    email.attach(f"factura_{order.id}.pdf", pdf_file.getvalue(), 'application/pdf')
    email.send()

    # Email admin 
    admin_recipients = [e for _, e in getattr(settings, "ADMINS", [])] or [settings.DEFAULT_FROM_EMAIL]
    if admin_recipients:
        # Construim delivery_info 
        delivery_info = ""
        if order.delivery_type == "delivery":
            delivery_info += f"<p><b>Adresa:</b> {order.address or '-'}, {order.city or ''} {order.zip_code or ''}</p>"
        delivery_info += f"<p><b>Tip livrare:</b> {order.get_delivery_type_display()}</p>"
        if order.desired_delivery_date:
            delivery_info += f"<p><b>Data livrare:</b> {order.desired_delivery_date}</p>"
        if order.delivery_time_slot:
            delivery_info += f"<p><b>Interval livrare:</b> {order.delivery_time_slot}</p>"
        if order.delivery_notes:
            delivery_info += f"<p><b>Note:</b> {order.delivery_notes}</p>"

        admin_html_message = render_to_string(
            'emails/new_order_email.html',
            {
                'order': order,
                'delivery_info': delivery_info, 
            }
        )

        admin_subject = f"Noua Comanda plasata - #{order.id}"
        admin_email = EmailMessage(
            subject=admin_subject,
            body=admin_html_message,
            from_email=from_email,
            to=admin_recipients,
            reply_to=[email_destination] if email_destination else None,
        )
        admin_email.content_subtype = "html"
        admin_email.attach(f"factura_{order.id}.pdf", pdf_file.getvalue(), 'application/pdf')
        admin_email.send()


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


@staff_member_required
def admin_dashboard(request):
    categories = Product.objects.values_list('category', flat=True).distinct()

    # Statistici comenzi si venituri ultimele 30 zile vs 30 zile precedente
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

#statistici vanzari si vizite
def sales_data_api(request):
    start = request.GET.get("start")
    end = request.GET.get("end")
    category = request.GET.get("category")
    product = request.GET.get("product")
    compare_start = request.GET.get("compare_start")
    compare_end = request.GET.get("compare_end")
  

    today = datetime.today().date()
    start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else today - timedelta(days=30)
    end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else today

    orders = Order.objects.filter(created_at__date__range=(start_date, end_date))

    # Filtru categorie/produs
    if category:
        orders = orders.filter(items__product__category=category)
    if product:
        orders = orders.filter(items__product__id=product)

    # Vanzari in timp
    daily_sales = (
        orders.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(total=Sum("total_price"), count=Count("id"))
        .order_by("day")
    )
    labels = [d["day"].strftime("%Y-%m-%d") for d in daily_sales]
    totals = [float(d["total"]) for d in daily_sales]
    counts = [d["count"] for d in daily_sales]

    # Top produse
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

    # Vizite 
    visits = (
        VisitorLog.objects
        .filter(timestamp__date__range=(start_date, end_date))
        .annotate(date=TruncDate('timestamp'))
        .values('date')
        .annotate(count=Count('session_key', distinct=True))
        .order_by('date')
    )
    visits_data = {v['date'].strftime("%Y-%m-%d"): v['count'] for v in visits}
    visit_counts = [visits_data.get(label, 0) for label in labels]

    # Rata conversiei
    conversion_rates = []
    for sales_count, visit_count in zip(counts, visit_counts):
        conversion_rates.append(round((sales_count / visit_count) * 100, 2) if visit_count > 0 else 0)

    # Comparare perioade
    compare = None
    if compare_start and compare_end:
        cs = datetime.strptime(compare_start, "%Y-%m-%d").date()
        ce = datetime.strptime(compare_end, "%Y-%m-%d").date()
        compare_orders = Order.objects.filter(created_at__date__range=(cs, ce))
        if category:
            compare_orders = compare_orders.filter(items__product__category=category)
        if product:
            compare_orders = compare_orders.filter(items__product__id=product)
        compare_daily_sales = (
            compare_orders.annotate(day=TruncDate("created_at"))
            .values("day")
            .annotate(total=Sum("total_price"), count=Count("id"))
            .order_by("day")
        )
        compare_labels = [d["day"].strftime("%Y-%m-%d") for d in compare_daily_sales]
        compare_totals = [float(d["total"]) for d in compare_daily_sales]
        compare = {"labels": compare_labels, "totals": compare_totals}
    return JsonResponse({
        "sales": {"labels": labels, "totals": totals, "counts": counts},
        "top_products": {"labels": top_product_names, "data": top_product_sold},
        "visits": {"labels": labels, "counts": visit_counts},
        "conversion": {"labels": labels, "rates": conversion_rates},
        "compare": compare,
    })

@require_GET
def sales_summary_api(request):
    # extragem din url datele 
    start = request.GET.get("start")
    end = request.GET.get("end")
    today = datetime.today().date()
    start_date = datetime.strptime(start, "%Y-%m-%d").date() if start else today - timedelta(days=30)
    end_date = datetime.strptime(end, "%Y-%m-%d").date() if end else today

    #calcul perioada anterioara
    prev_start = start_date - (end_date - start_date)
    prev_end = start_date
    
    #comenzile din perioada curenta si anterioara
    orders_qs = Order.objects.filter(created_at__date__range=(start_date, end_date))
    prev_orders_qs = Order.objects.filter(created_at__date__range=(prev_start, prev_end))

    #nr total de comenzi acum si precedent + cresterea in procente
    total_orders = orders_qs.count()
    prev_orders = prev_orders_qs.count()
    orders_growth = ((total_orders - prev_orders) / prev_orders * 100) if prev_orders else None

    # Calculam venitul total + cresterea in procente
    total_revenue = orders_qs.aggregate(total=Sum('total_price'))['total'] or 0
    prev_revenue = prev_orders_qs.aggregate(total=Sum('total_price'))['total'] or 0
    revenue_growth = ((total_revenue - prev_revenue) / prev_revenue * 100) if prev_revenue else None

    # Formatam total_revenue la 2 zecimale
    total_revenue = round(float(total_revenue), 2)

    return JsonResponse({
        "total_orders": total_orders,
        "orders_growth": orders_growth,
        "total_revenue": total_revenue,
        "revenue_growth": revenue_growth,
    })


