// Preserve position only when an action returns to the same page. A one-time
// value avoids reopening unrelated pages at an old scroll position.
if ('scrollRestoration' in history) history.scrollRestoration = 'manual';
const pendingScrollKey = 'greencart-pending-scroll';
try {
  const pendingScroll = JSON.parse(sessionStorage.getItem(pendingScrollKey));
  sessionStorage.removeItem(pendingScrollKey);
  if (pendingScroll?.path === location.pathname) {
    requestAnimationFrame(() => window.scrollTo({ top: Number(pendingScroll.y), behavior: 'auto' }));
  }
} catch {
  sessionStorage.removeItem(pendingScrollKey);
}
const rememberScroll = () => sessionStorage.setItem(pendingScrollKey, JSON.stringify({
  path: location.pathname,
  y: window.scrollY,
}));
document.addEventListener('click', (event) => {
  const link = event.target.closest('a[href]');
  if (link && link.origin === location.origin && link.pathname === location.pathname && !link.target && !link.hasAttribute('download')) {
    rememberScroll();
  }
});
document.addEventListener('submit', (event) => {
  if (!event.target.matches('[data-add-cart]')) rememberScroll();
}, true);

// Let the document keep scrolling when the pointer is over an embedded map.
// Ctrl+wheel remains available for deliberate map zooming.
document.addEventListener('wheel', (event) => {
  if (event.target.closest('.leaflet-container') && !event.ctrlKey) {
    event.preventDefault();
    event.stopImmediatePropagation();
    window.scrollBy({ top: event.deltaY, behavior: 'auto' });
  }
}, { capture: true, passive: false });

const menuButton = document.querySelector('#menu-button');
const mobileMenu = document.querySelector('#mobile-menu');
if (menuButton && mobileMenu) {
  menuButton.addEventListener('click', () => {
    const open = mobileMenu.classList.toggle('hidden') === false;
    menuButton.setAttribute('aria-expanded', String(open));
  });
}
const dismissToast = (toast) => {
  if (!toast || toast.dataset.closing) return;
  toast.dataset.closing = 'true';
  toast.classList.add('toast-leave');
  setTimeout(() => toast.remove(), 230);
};
const showToast = (message, type = 'success') => {
  let container = document.querySelector('#toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toast-container';
    container.className = 'fixed right-4 top-24 z-[100] flex w-[calc(100%-2rem)] max-w-sm flex-col gap-3 sm:right-6';
    container.setAttribute('aria-live', 'polite');
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  const color = type === 'danger'
    ? 'border-rose-200 text-rose-800'
    : type === 'info'
      ? 'border-sky-200 text-sky-800'
      : 'border-emerald-200 text-emerald-800';
  toast.className = `flash-message toast-enter flex items-start justify-between rounded-2xl border bg-white p-4 text-sm font-semibold shadow-2xl ${color}`;
  const text = document.createElement('span');
  text.className = 'flex gap-3';
  text.innerHTML = '<span class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full bg-current"></span>';
  const messageNode = document.createElement('span');
  messageNode.textContent = message;
  text.appendChild(messageNode);
  const close = document.createElement('button');
  close.type = 'button'; close.className = 'ml-4 text-xl leading-none opacity-50 hover:opacity-100'; close.textContent = '×';
  close.addEventListener('click', () => dismissToast(toast));
  toast.append(text, close); container.appendChild(toast);
  setTimeout(() => dismissToast(toast), 3000);
};
document.querySelectorAll('.flash-message').forEach((toast, index) => {
  toast.querySelector('button')?.addEventListener('click', () => dismissToast(toast));
  setTimeout(() => dismissToast(toast), 3000 + (index * 250));
});

const confirmDialog = document.querySelector('#confirm-dialog');
if (confirmDialog) {
  let pendingForm = null;
  let pendingSubmitter = null;
  document.addEventListener('submit', (event) => {
    const form = event.target.closest('form[data-confirm]');
    if (!form || form.dataset.confirmed === 'true') {
      if (form) delete form.dataset.confirmed;
      return;
    }
    event.preventDefault();
    pendingForm = form;
    pendingSubmitter = event.submitter || null;
    confirmDialog.querySelector('[data-confirm-title]').textContent = form.dataset.confirmTitle || 'Confirm action';
    confirmDialog.querySelector('[data-confirm-message]').textContent = form.dataset.confirmMessage || 'Are you sure you want to continue?';
    confirmDialog.querySelector('[data-confirm-accept]').textContent = form.dataset.confirmLabel || 'Confirm';
    confirmDialog.showModal();
  });
  confirmDialog.querySelector('[data-confirm-cancel]').addEventListener('click', () => {
    pendingForm = null; pendingSubmitter = null; confirmDialog.close();
  });
  confirmDialog.querySelector('[data-confirm-accept]').addEventListener('click', () => {
    if (!pendingForm) return;
    const form = pendingForm, submitter = pendingSubmitter;
    pendingForm = null; pendingSubmitter = null; confirmDialog.close();
    form.dataset.confirmed = 'true';
    submitter ? form.requestSubmit(submitter) : form.requestSubmit();
  });
  confirmDialog.addEventListener('click', (event) => {
    if (event.target === confirmDialog) {
      pendingForm = null; pendingSubmitter = null; confirmDialog.close();
    }
  });
}

document.querySelectorAll('[data-stepper]').forEach((stepper) => {
  const input = stepper.querySelector('input[type="number"]');
  stepper.querySelector('[data-minus]')?.addEventListener('click', () => {
    input.value = Math.max(Number(input.min || 0), Number(input.value || 0) - 1);
    input.dispatchEvent(new Event('change', { bubbles: true }));
    if (stepper.hasAttribute('data-auto-submit')) stepper.requestSubmit();
  });
  stepper.querySelector('[data-plus]')?.addEventListener('click', () => {
    input.value = Math.min(Number(input.max || Infinity), Number(input.value || 0) + 1);
    input.dispatchEvent(new Event('change', { bubbles: true }));
    if (stepper.hasAttribute('data-auto-submit')) stepper.requestSubmit();
  });
});

document.querySelectorAll('[data-category-form]').forEach((form) => {
  const existing = form.querySelector('[data-existing-category]');
  const newCategory = form.querySelector('[data-new-category]');
  const existingInput = existing?.querySelector('[name="category"]');
  const newInput = newCategory?.querySelector('[name="new_category"]');
  const syncCategoryMode = () => {
    const createNew = form.querySelector('[name="category_mode"]:checked')?.value === 'new';
    existing?.classList.toggle('hidden', createNew);
    newCategory?.classList.toggle('hidden', !createNew);
    if (existingInput) existingInput.required = !createNew;
    if (newInput) newInput.required = createNew;
  };
  form.querySelectorAll('[name="category_mode"]').forEach((radio) => {
    radio.addEventListener('change', syncCategoryMode);
  });
  syncCategoryMode();
});

document.querySelectorAll('input[type="file"][accept*="image"]').forEach((input) => {
  const form = input.closest('form');
  const profilePhoto = input.name === 'profile_image' ? document.querySelector('[data-profile-photo-preview]') : null;
  const profileFallback = input.name === 'profile_image' ? document.querySelector('[data-profile-photo-fallback]') : null;
  const productPhoto = input.name === 'image' ? form?.querySelector('[data-product-image-preview]') : null;
  const productFallback = input.name === 'image' ? form?.querySelector('[data-product-image-fallback]') : null;
  const directPhoto = profilePhoto || productPhoto;
  const directFallback = profileFallback || productFallback;
  const originalSrc = directPhoto?.getAttribute('src') || '';
  const removeInput = form?.querySelector(input.name === 'profile_image' ? '[name="remove_profile_image"]' : '[name="remove_product_image"]');
  let preview = null;
  let previewImage = null;
  if (!directPhoto) {
    preview = document.createElement('div');
    preview.className = 'mt-3 hidden items-center gap-3 rounded-2xl border border-emerald-200 bg-emerald-50 p-3';
    previewImage = document.createElement('img');
    previewImage.className = 'h-20 w-20 shrink-0 rounded-xl object-cover shadow-sm';
    previewImage.alt = 'Selected image preview';
    const copy = document.createElement('div');
    copy.innerHTML = '<p class="text-sm font-extrabold text-emerald-800">Preview ready</p><p class="mt-1 text-xs text-emerald-700">This image is temporary until you save the form.</p>';
    preview.append(previewImage, copy);
    input.insertAdjacentElement('afterend', preview);
  }
  const showDirectPhoto = (src) => {
    if (!directPhoto) return;
    directPhoto.src = src;
    directPhoto.classList.remove('hidden');
    directFallback?.classList.add('hidden');
    directFallback?.classList.remove('grid');
  };
  const resetDirectPhoto = () => {
    if (!directPhoto) return;
    if (originalSrc) {
      showDirectPhoto(originalSrc);
    } else {
      directPhoto.removeAttribute('src');
      directPhoto.classList.add('hidden');
      directFallback?.classList.remove('hidden');
      directFallback?.classList.add('grid');
    }
  };
  const hidePreview = () => {
    preview?.classList.add('hidden');
    preview?.classList.remove('flex');
  };
  let objectUrl;
  input.addEventListener('change', () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    const file = input.files?.[0];
    if (!file) {
      hidePreview();
      resetDirectPhoto();
      return;
    }
    if (!file.type.startsWith('image/') || file.size > 5 * 1024 * 1024) {
      input.value = '';
      hidePreview();
      resetDirectPhoto();
      showToast(file.size > 5 * 1024 * 1024 ? 'Choose an image smaller than 5 MB.' : 'Choose a supported image file.', 'danger');
      return;
    }
    objectUrl = URL.createObjectURL(file);
    if (preview && previewImage) {
      previewImage.src = objectUrl;
      preview.classList.remove('hidden');
      preview.classList.add('flex');
    }
    showDirectPhoto(objectUrl);
    if (removeInput) removeInput.checked = false;
    showToast(`${file.name} is ready to preview. Save the form to keep it.`, 'info');
  });
  removeInput?.addEventListener('change', () => {
    if (removeInput.checked) {
      input.value = '';
      if (objectUrl) URL.revokeObjectURL(objectUrl);
      hidePreview();
      if (directPhoto) {
        directPhoto.removeAttribute('src');
        directPhoto.classList.add('hidden');
        directFallback?.classList.remove('hidden');
        directFallback?.classList.add('grid');
      }
    } else {
      resetDirectPhoto();
    }
  });
});

document.querySelectorAll('form[data-add-cart]').forEach((form) => {
  form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"], button:not([type])');
    const original = button.textContent;
    button.disabled = true; button.textContent = 'Adding…';
    try {
      const response = await fetch(form.action, {
        method: 'POST', body: new FormData(form),
        headers: {'X-Requested-With': 'XMLHttpRequest'}
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.message || 'Unable to add item.');
      document.querySelectorAll('[data-cart-count]').forEach((node) => node.textContent = data.cart_count);
      showToast(data.message);
    } catch (error) { showToast(error.message, 'danger'); }
    finally { button.disabled = false; button.textContent = original; }
  });
});
