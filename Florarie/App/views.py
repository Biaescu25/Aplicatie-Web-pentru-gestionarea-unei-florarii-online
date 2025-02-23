from django.shortcuts import render
from .models import Product

def home(request):
    products = Product.objects.all()
    return render(request, "home.html", {'products': products})

def load_more(request):
    return render(request, "partials/more_content.html")