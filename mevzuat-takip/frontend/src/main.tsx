// Arayüzün başlangıç noktası, React'i sayfadaki <div id="root"> içine yerleştirir.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Tailwind stilleri.
import './index.css'
import App from './App.tsx'

// Uygulamayı (App) çiz. StrictMode, geliştirmede olası hataları erken gösteren React modu.
createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
