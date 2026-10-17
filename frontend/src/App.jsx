import { Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import ProtectedRoute from './components/ProtectedRoute'
import InboxPage from './pages/InboxPage'
import MessagePage from './pages/MessagePage'
import ComposePage from './pages/ComposePage'
import FolderPage from './pages/FolderPage'
import SettingsPage from './pages/SettingsPage'
import AccountsPage from './pages/AccountsPage'
import AddAccountPage from './pages/AddAccountPage'
import Login from './pages/Login'
import { MailProvider } from './contexts/MailContext'
import { ShizuhaChatWidget } from './components/chat'
import { Toaster } from 'react-hot-toast'

function App() {
  return (
    <>
      <Routes>
        {/* Standalone login page — "Sign in with Shizuha ID" (PLAT-353) */}
        <Route path="/login" element={<Login />} />
        <Route path="/" element={
          <ProtectedRoute>
            <MailProvider>
              <Layout />
            </MailProvider>
          </ProtectedRoute>
        }>
          <Route index element={<Navigate to="/inbox" replace />} />
          <Route path="inbox" element={<InboxPage />} />
          <Route path="sent" element={<FolderPage folderRole="sent" />} />
          <Route path="drafts" element={<FolderPage folderRole="drafts" />} />
          <Route path="trash" element={<FolderPage folderRole="trash" />} />
          <Route path="folder/:folderId" element={<FolderPage />} />
          <Route path="message/:messageId" element={<MessagePage />} />
          <Route path="compose" element={<ComposePage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="accounts" element={<AccountsPage />} />
          <Route path="accounts/add" element={<AddAccountPage />} />
        </Route>
      </Routes>
      <ShizuhaChatWidget
        sourceService="mail"
        welcomeMessage="Hi! I can help you search emails, compose messages, and manage your inbox."
        showToolCalls={true}
      />
      <Toaster
        position="bottom-center"
        toastOptions={{
          duration: 3000,
          style: { background: '#333', color: '#fff' },
          success: { iconTheme: { primary: '#22c55e', secondary: '#fff' } },
          error: { duration: 5000, iconTheme: { primary: '#ef4444', secondary: '#fff' } },
        }}
      />
    </>
  )
}

export default App
