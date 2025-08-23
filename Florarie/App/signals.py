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
    # Only delete custom bouquet if this product was a custom bouquet product
    if hasattr(instance, 'linked_custom_bouquet') and instance.linked_custom_bouquet:
        try:
            bouquet = instance.linked_custom_bouquet
            # Set product to None first to break the circular reference
            bouquet.product = None
            bouquet.save()
            bouquet.delete()
        except CustomBouquet.DoesNotExist:
            pass

@receiver(post_delete, sender=CustomBouquet)
def delete_related_product(sender, instance, **kwargs):
    # Only delete product if it exists and is a custom product
    try:
        # Safely check if the product exists and is a custom product
        if hasattr(instance, 'product') and instance.product and instance.product.is_custom:
            # Set the custom_bouquet reference to None first to break circular reference
            instance.product.linked_custom_bouquet = None
            instance.product.save()
            instance.product.delete()
    except Product.DoesNotExist:
        # Product was already deleted, nothing to do
        pass
    except Exception as e:
        # Handle any other unexpected errors
        print(f"Error in delete_related_product signal: {e}")
        pass