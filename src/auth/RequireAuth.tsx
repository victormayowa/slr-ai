import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { useAuth } from './authContext';

// Sends signed-out visitors to the login page, remembering where they were headed.
export function RequireAuth() {
  const { token } = useAuth();
  const location = useLocation();
  if (!token) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}
