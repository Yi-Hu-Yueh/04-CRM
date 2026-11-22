document.querySelectorAll('[data-cancel]').forEach(button => button.addEventListener('click', () => {
  button.closest('form').reset();
  window.location.assign(button.dataset.cancel);
}));
document.querySelectorAll('form.editor').forEach(form => form.addEventListener('submit', () => {
  form.querySelectorAll('button').forEach(button => { button.disabled = true; });
}));
