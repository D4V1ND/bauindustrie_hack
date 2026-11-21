// Where the frontend finds the backend (lv/api.py).
// Empty: http://localhost:8000 when opened locally, otherwise "/api" (proxied by vercel.json).
// Set a full URL to call the backend directly, e.g. window.LV_API = 'https://lv-api.example.com';
// A visitor can also override it once with ?api=https://... in the page address.
window.LV_API = 'https://api-production-4dd4519e.up.railway.app';
