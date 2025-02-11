from django.shortcuts import render

def home(request):
    return render(request, "home.html")

def load_more(request):
    return render(request, "partials/more_content.html")