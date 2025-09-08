from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from ckeditor.fields import RichTextField


class Product(models.Model):
    name = models.CharField(max_length=100, null=False, blank=False)
    description = RichTextField()
    category = models.CharField(
        max_length=100,
        choices=[
            ("buchete", "buchete"),
            ("plante", "plante"),
            ("aranjamente", "aranjamente"),
            ("Accessories", "Accessories"),
            ("Gifts", "Gifts"),
            ("CustomBouquet", "CustomBouquet"),
        ],
        default="General"
    )
    price = models.DecimalField(max_digits=10, decimal_places=0, null=False, blank=False)
    image = models.ImageField(upload_to="Pictures/", null=False, blank=False)
    created_at = models.DateTimeField(auto_now_add=True, null=False, blank=False)

    stock = models.PositiveIntegerField(default=0)

    is_custom = models.BooleanField(default=False)
 
    in_store = models.BooleanField(default=False)
    auction_manual = models.BooleanField(default=False)
    auction_start_time = models.DateTimeField(null=True, blank=True)
    auction_floor_price = models.DecimalField(max_digits=10, decimal_places=2, default=10)
    auction_interval_minutes = models.PositiveIntegerField(default=60)
    auction_drop_amount = models.DecimalField(max_digits=6, decimal_places=2, default=5)

    before_auction_price = models.DecimalField(max_digits=10, decimal_places=2, null=False, blank=False, default=0.00) 
    bid_submited = models.BooleanField(default=False)  
    number_of_purcheses = models.PositiveIntegerField(default=0)  

    def clean(self):
     
            if self.auction_manual:
                if not self.auction_start_time:
                    raise ValidationError("Timpul de început al licitației este necesar atunci când produsul este în magazin.")
                if not self.auction_floor_price:
                    raise ValidationError("Prețul minim al licitației este necesar atunci când produsul este în magazin.")
                if not self.auction_interval_minutes:
                    raise ValidationError("Intervalul de licitație în minute este necesar atunci când produsul este în magazin.")
                if not self.auction_drop_amount:
                    raise ValidationError("Suma de scădere a licitației este necesară atunci când produsul este în magazin.")

    def is_in_auction(self):
        if not self.before_auction_price or self.before_auction_price == 0:
            self.before_auction_price = self.price
        return self.auction_manual 

    def is_auction_expired(self):
        if not self.auction_start_time:
            return False
        
        expiry_time = self.auction_start_time + timedelta(hours=24)
        return timezone.now() > expiry_time

    def get_auction_price(self):
        if not self.is_in_auction():
            return self.price, 0, 0 
 
        minutes_passed = (timezone.now() - self.auction_start_time).total_seconds() / 60
        total_drops = max(1, int(minutes_passed / self.auction_interval_minutes))  # numărul minim de scăderi este 1
        discount = min(self.auction_drop_amount * total_drops, self.price - self.auction_floor_price)

        auction_bid_price = self.price - Decimal(discount)  # Calculează prețul licitației

        # Calculează procentajul pe baza before_auction_price pentru consistență cu UI
        if self.before_auction_price == 0:
            percentage_reduction = Decimal(0)
        else:
            # Calculează procentajul față de prețul original înainte de licitație
            percentage_reduction = Decimal((self.before_auction_price - auction_bid_price) / self.before_auction_price * 100)
         
        return auction_bid_price, discount, percentage_reduction
    
    def __str__(self):
        return self.name

    def get_available_stock(self):
        from django.db.models import Sum
        
        # Skip calcule pentru produse care nu sunt gestionate prin inventar
        if self.category in ['buchete', 'aranjamente', 'CustomBouquet']:
            return 10  
        # Calculam stocul disponibil pentru produse gestionate prin inventar
        reserved = CartItem.objects.filter(
            product=self,
            reserved_until__gt=timezone.now()
        ).aggregate(total=Sum('quantity'))['total'] or 0
        
        # returneaza stocul disponibil
        return max(0, self.stock - reserved)

class CartItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    session_id = models.CharField(max_length=255, null=True, blank=True) #pentru guests
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    reserved_until = models.DateTimeField(null=True, blank=True)

    def is_expired(self):
        return self.reserved_until and timezone.now() > self.reserved_until
    
    def total_price(self):
        return self.product.price * self.quantity
    
    def refresh_reservation(self):
        self.reserved_until = timezone.now() + timedelta(minutes=30)
        self.save()

class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    address = models.TextField(blank=True, null=True)  
    phone_number = models.CharField(max_length=15)
    city = models.CharField(max_length=100, blank=True, null=True)  
    zip_code = models.CharField(max_length=10, blank=True, null=True)  
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=[("card", "Card"), ("cash", "Cash on Delivery")], default="card")
    payment_status = models.BooleanField(default=False)  
    created_at = models.DateTimeField(auto_now_add=True)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=29.00) 
    delivery_type = models.CharField(max_length=20, choices=[("delivery", "Livrare la adresă"), ("pickup", "Ridicare personală")], default="delivery")
    desired_delivery_date = models.DateField(null=True, blank=True)
    delivery_time_slot = models.CharField(max_length=20, choices=[
        ("09:00-11:00", "09:00 - 11:00"),
        ("11:00-13:00", "11:00 - 13:00"),
        ("13:00-15:00", "13:00 - 15:00"),
        ("15:00-17:00", "15:00 - 17:00"),
        ("17:00-19:00", "17:00 - 19:00"),
        ("19:00-21:00", "19:00 - 21:00"),
    ], null=True, blank=True)
    delivery_notes = models.TextField(blank=True, null=True)

    def get_delivery_fee(self):
        from decimal import Decimal
        if self.delivery_type == "pickup":
            return Decimal('0.00')
        return Decimal('29.00')  
    
    def final_total(self):
        return self.total_price + self.get_delivery_fee() 

class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name='items',on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)  

    def __str__(self):
        return f"{self.quantity} x {self.product.name}"
    
class Payment(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    transaction_id = models.CharField(max_length=255, unique=True)
    status = models.CharField(max_length=20, choices=[("Pending", "Pending"), ("Completed", "Completed")])
    created_at = models.DateTimeField(auto_now_add=True)
    payment_method = models.CharField(
        max_length=20,
        choices=[("card", "Card"), ("cash", "Cash on Delivery")],
        default="card"  
    )

    def __str__(self):
        return f"Payment {self.transaction_id or 'Cash'} - {self.status}"
    
    
class BouquetShape(models.Model):
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to='bouquet_shapes/')
    def __str__(self):
        return self.name
    
class Flower(models.Model):
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to='flowers/')
    price = models.DecimalField(max_digits=6, decimal_places=2)
    
    def __str__(self):
        return self.name

class Greenery(models.Model):
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to='greens/')
    price = models.DecimalField(max_digits=6, decimal_places=2)
    def __str__(self):
        return self.name

class WrappingColor(models.Model):
    name = models.CharField(max_length=50)
    hex = models.CharField(max_length=7)  # e.g. "#FFFFFF"

    def __str__(self):
        return self.name

class WrappingPaper(models.Model):
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to='wrappings/')
    price = models.DecimalField(max_digits=6, decimal_places=2)
    colors = models.ManyToManyField('WrappingColor', through='WrappingPaperColor')
    in_stock = models.BooleanField(default=True)

    def is_in_stock(self):
        return any(variant.quantity > 0 for variant in self.color_variants.all())
class WrappingPaperColor(models.Model):
    wrapping_paper = models.ForeignKey('WrappingPaper', on_delete=models.CASCADE, related_name='color_variants')
    color = models.ForeignKey('WrappingColor', on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=0)

    @property
    def in_stock(self):
        return self.quantity > 0
    
    def __str__(self):
         return f"{self.wrapping_paper.name} - {self.color.name} ({self.quantity} disponibile)"

class CustomBouquet(models.Model):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    shape = models.ForeignKey(BouquetShape, on_delete=models.SET_NULL, null=True)
    flowers = models.ManyToManyField(Flower, through="BouquetFlower")
    greenery = models.ManyToManyField(Greenery, blank=True)
    wrapping = models.ForeignKey(WrappingPaper, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed = models.BooleanField(default=False)
    product = models.OneToOneField(
        Product, on_delete=models.CASCADE, null=True, blank=True, related_name="linked_custom_bouquet"
    )  

    def related_flowers(self):
        return ", ".join([f"{bf.quantity} x {bf.flower.name}" for bf in self.bouquetflower_set.all()])
    related_flowers.short_description = "Flowers"

class BouquetFlower(models.Model):
    bouquet = models.ForeignKey(CustomBouquet, on_delete=models.CASCADE)
    flower = models.ForeignKey(Flower, on_delete=models.CASCADE)
    quantity = models.IntegerField()


class ContactMessage(models.Model):
    name = models.CharField(max_length=100)
    email = models.EmailField()
    message = models.TextField()
    phone = models.CharField(max_length=15, null=True, blank=True)  # Optional phone field
    submitted_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message from {self.name} ({self.email})"


class VisitorLog(models.Model):
    ip = models.GenericIPAddressField()
    path = models.CharField(max_length=255)
    session_key = models.CharField(max_length=40, db_index=True, null=True, blank=True)  # new
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['timestamp']),
            models.Index(fields=['path']),
            models.Index(fields=['session_key', 'timestamp']),
            models.Index(fields=['session_key', 'path', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.timestamp.strftime('%Y-%m-%d %H:%M:%S')} - {self.session_key or 'no-session'} - {self.path}"