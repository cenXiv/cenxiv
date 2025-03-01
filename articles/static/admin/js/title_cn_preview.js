function previewMathJax(button) {
    const input = button.parentElement.previousElementSibling;
    const preview = button.parentElement.parentElement.nextElementSibling.querySelector('.mathjax-preview');

    preview.innerHTML = `<div class="mathjax">${input.value}</div>`;
    preview.style.display = 'block';

    if (window.MathJax) {
        MathJax.typesetPromise([preview]);
    }
}