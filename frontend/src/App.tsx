import { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import Layout from "./components/Layout";
import { Loading } from "./components/common";
import Admin from "./pages/Admin";
import BatchPage from "./pages/BatchPage";
import Batches from "./pages/Batches";
import ChangePassword from "./pages/ChangePassword";
import DocumentPage from "./pages/DocumentPage";
import History from "./pages/History";
import Login from "./pages/Login";
import NewDocument from "./pages/NewDocument";

function Protected({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user, ready } = useAuth();
  const loc = useLocation();
  if (!ready) return <Loading />;
  if (!user) return <Navigate to="/connexion" state={{ from: loc.pathname }} replace />;
  if (user.must_change_password && loc.pathname !== "/mot-de-passe") return <Navigate to="/mot-de-passe" replace />;
  if (admin && user.role !== "admin") return <Navigate to="/" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/connexion" element={<Login />} />
      <Route path="/mot-de-passe" element={<Protected><ChangePassword /></Protected>} />
      <Route element={<Protected><Layout /></Protected>}>
        <Route index element={<NewDocument />} />
        <Route path="documents" element={<History />} />
        <Route path="historique" element={<Navigate to="/documents" replace />} />
        <Route path="documents/:id" element={<DocumentPage />} />
        <Route path="lots" element={<Batches />} />
        <Route path="lots/:id" element={<BatchPage />} />
        <Route path="administration" element={<Protected admin><Admin /></Protected>} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
