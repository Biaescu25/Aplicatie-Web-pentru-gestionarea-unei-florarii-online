from django.contrib.auth.models import AbstractUser, Group, Permission, User
from django.db import models
from django.utils import timezone
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError

# Create your models here.

# class User(AbstractUser):
#     groups = models.ManyToManyField(
#         Group,
#         related_name='custom_user_set',  # Add related_name to avoid conflict
#         blank=True,
#         help_text='The groups this user belongs to.',
#         verbose_name='groups',
#     )
#     user_permissions = models.ManyToManyField(
#         Permission,
#         related_name='custom_user_permissions_set',  # Add related_name to avoid conflict
#         blank=True,
#         help_text='Specific permissions for this user.',
#         verbose_name='user permissions',
#     )


class Product(models.Model):
    name = models.CharField(max_length=100, null=False, blank=False)
    description = models.TextField()
    category = models.CharField(
        max_length=100,
        choices=[
            ("Flowers", "Flowers"),
            ("Bouquets", "Bouquets"),
            ("Accessories", "Accessories"),
            ("Gifts", "Gifts"),
            ("CustomBouquet", "CustomBouquet"),
        ],
        default="General"
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, null=False, blank=False)
    image = models.ImageField(upload_to="Pictures/", null=False, blank=False)
    created_at = models.DateTimeField(auto_now_add=True, null=False, blank=False)

    is_custom = models.BooleanField(default=False)
    #custom_bouquet = models.OneToOneField("CustomBouquet", on_delete=models.CASCADE, null=True, blank=True, related_name="linked_product")  # Add related_name to avoid conflict

    # NEW FIELDS
    in_store = models.BooleanField(default=False)
    auction_manual = models.BooleanField(default=False)
    auction_start_time = models.DateTimeField(null=True, blank=True)
    auction_floor_price = models.DecimalField(max_digits=10, decimal_places=2, default=10)
    auction_interval_minutes = models.PositiveIntegerField(default=60)
    auction_drop_amount = models.DecimalField(max_digits=6, decimal_places=2, default=5)

    before_auction_price = models.DecimalField(max_digits=10, decimal_places=2, null=False, blank=False, default=0.00)  # Store the price before auction
    bid_submited = models.BooleanField(default=False)  # Track if a bid has been submitted

    def clean(self):
        if self.in_store:
            if not self.auction_start_time:
                raise ValidationError("Auction start time is required when the product is in store.")
            if not self.auction_floor_price:
                raise ValidationError("Auction floor price is required when the product is in store.")
            if not self.auction_interval_minutes:
                raise ValidationError("Auction interval minutes are required when the product is in store.")
            if not self.auction_drop_amount:
                raise ValidationError("Auction drop amount is required when the product is in store.")


    def is_in_auction(self):
        # Eligible if manually added or in store for > 3 days
        self.before_auction_price = self.price
        return self.auction_manual or (self.in_store and self.created_at <= timezone.now() - timedelta(minutes=3)) and self.bid_submited == False

    def get_auction_price(self):
        if not self.is_in_auction():
            return self.price
 
        minutes_passed = (timezone.now() - self.auction_start_time).total_seconds() / 60
        discount = min(0.05 * int(minutes_passed / self.auction_interval_minutes), 0.50)  # 5% per interval, up to 50%
        discount_decimal = Decimal(str(discount))

        auction_bid_price = self.price * (Decimal("1.0") - discount_decimal) # Calculate the auction price
         
        return auction_bid_price
    
    def __str__(self):
        return self.name

class CartItem(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    session_id = models.CharField(max_length=255, null=True, blank=True) # Store session ID for anonymous users
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1)
    
    def total_price(self):
        return self.product.price * self.quantity
    
class Order(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True)
    session_id = models.CharField(max_length=255, null=True, blank=True)
    full_name = models.CharField(max_length=255)
    email = models.EmailField()
    address = models.TextField()
    phone_number = models.CharField(max_length=15)
    city = models.CharField(max_length=100)
    zip_code = models.CharField(max_length=10)
    total_price = models.DecimalField(max_digits=10, decimal_places=2)
    payment_method = models.CharField(max_length=20, choices=[("card", "Card"), ("cash", "Cash on Delivery")], default="card")
    payment_status = models.BooleanField(default=False)  # False = Not paid, True = Paid
    created_at = models.DateTimeField(auto_now_add=True)
    delivery_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0.00)  # Default delivery fee

    def final_total(self):
        return self.total_price + self.delivery_fee  # Calculate final amount

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
        default="card"  # Provide a default value
    )

    def __str__(self):
        return f"Payment {self.transaction_id or 'Cash'} - {self.status}"
    
    
#Custom bouquet models
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

class WrappingPaper(models.Model):
    name = models.CharField(max_length=100)
    image = models.ImageField(upload_to='wrappings/')
    price = models.DecimalField(max_digits=6, decimal_places=2)
    def __str__(self):
        return self.name

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
    )  # Add related_name to avoid conflict

    def related_flowers(self):
        return ", ".join([f"{bf.quantity} x {bf.flower.name}" for bf in self.bouquetflower_set.all()])
    related_flowers.short_description = "Flowers"

class BouquetFlower(models.Model):
    bouquet = models.ForeignKey(CustomBouquet, on_delete=models.CASCADE)
    flower = models.ForeignKey(Flower, on_delete=models.CASCADE)
    quantity = models.IntegerField()