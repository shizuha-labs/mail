import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMail } from '../contexts/MailContext'

export default function Compose() {
  const navigate = useNavigate()
  const { openCompose } = useMail()

  useEffect(() => {
    // Open compose modal and navigate back
    openCompose()
    navigate('/inbox')
  }, [])

  return null
}
