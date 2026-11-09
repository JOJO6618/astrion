document.getElementById('apply').addEventListener('click', () => {
  document.getElementById('result').textContent = document.getElementById('message').value;
});
document.getElementById('file').addEventListener('change', event => {
  document.getElementById('file-result').textContent = event.target.files[0]?.name || '';
});
