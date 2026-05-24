import { NavLink, Outlet } from "react-router-dom";

const linkClass = ({ isActive }) => (isActive ? "nav-link active" : "nav-link");

export default function Layout() {
  return (
    <>
      <nav className="nav">
        <NavLink to="/conversations" className="nav-brand">
          <span className="mark">◎</span> lumino
        </NavLink>
        <div className="nav-links">
          <NavLink to="/conversations" className={linkClass}>
            Conversations
          </NavLink>
          <NavLink to="/traces" className={linkClass}>
            Traces
          </NavLink>
          <NavLink to="/dashboard" className={linkClass}>
            Dashboard
          </NavLink>
        </div>
      </nav>
      <Outlet />
    </>
  );
}
