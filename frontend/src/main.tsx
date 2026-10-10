import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './AgentApp'
import MobileApp from './remote/MobileApp'
import './styles.css'
import './agent.css'
import './workspace.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {location.pathname.startsWith('/mobile') ? <MobileApp /> : <App />}
  </StrictMode>,
)

