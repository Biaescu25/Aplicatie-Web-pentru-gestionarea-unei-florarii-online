from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from .models import CartItem

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

# shop/signals.py

from django.db.models.signals import post_delete
from django.dispatch import receiver
from .models import Product, CustomBouquet

@receiver(post_delete, sender=Product)
def delete_related_custom_bouquet(sender, instance, **kwargs):
    try:
        bouquet = instance.linked_custom_bouquet
        bouquet.delete()
    except CustomBouquet.DoesNotExist:
        pass

@receiver(post_delete, sender=CustomBouquet)
def delete_related_product(sender, instance, **kwargs):
    if instance.product:
        instance.product.delete()