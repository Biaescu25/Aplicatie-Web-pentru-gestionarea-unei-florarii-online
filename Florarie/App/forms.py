from django import forms
from django.contrib.auth.models import User
from .models import ContactMessage
from .models import Product


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if self.instance and self.instance.pk and self.instance.image:
            self.fields['image'].widget.attrs.update({
                'onchange': "previewImage(this)",
            })

            self.fields['image'].help_text = (
                f'<img id="image-preview" src="{self.instance.image.url}" '
                f'style="max-height: 150px; margin-top: 10px; object-fit: cover;" />'
            )

class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['first_name', 'last_name', 'email']

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            for field_name, field in self.fields.items():
                field.widget.attrs.update({
                    'class': 'mt-1 block w-full rounded-md border border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 p-2'
                })


class ContactForm(forms.ModelForm):
    class Meta:
        model = ContactMessage
        fields = ['name', 'email', 'message']
