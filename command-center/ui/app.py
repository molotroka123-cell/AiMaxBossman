"""Command Center Dashboard App with Gateway integration.

Displays:
- Provider stats (health, models count)
- Model selector dropdown
- Active task status
- Gateway connection status
"""
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
from .api import get_health, get_all_models, get_provider_stats, get_active_task


class DashboardApp:
    """Main dashboard application."""
    
    def __init__(self, root):
        self.root = root
        self.root.title("Bossman Command Center")
        self.root.geometry("1200x800")
        
        self.gateway_status = "disconnected"
        self.providers_data = {}
        self.all_models = []
        
        self._create_menu()
        self._create_status_bar()
        self._create_dashboard()
        self._refresh_dashboard()
    
    def _create_menu(self):
        menubar = tk.Menu(self.root)
        self.root.config(menu=menubar)
        
        file_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=file_menu)
        file_menu.add_command(label="Refresh", command=self._refresh_dashboard, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        
        gateway_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Gateway", menu=gateway_menu)
        gateway_menu.add_command(label="Refresh Models", command=self._refresh_models)
        gateway_menu.add_command(label="Settings", command=self._show_gateway_settings)
        
        help_menu = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=help_menu)
        help_menu.add_command(label="About", command=self._show_about)
        
        self.root.bind("<F5>", lambda e: self._refresh_dashboard())
    
    def _create_status_bar(self):
        self.status_bar = ttk.Label(
            self.root,
            text="Gateway: Disconnected | Providers: 0 | Models: 0",
            relief=tk.SUNKEN,
            anchor=tk.W
        )
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)
    
    def _create_dashboard(self):
        main_frame = ttk.Frame(self.root, padding=10)
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        left_panel = ttk.LabelFrame(main_frame, text="Providers", padding=10)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        self.provider_tree = ttk.Treeview(left_panel, columns=("Status", "Models"), show="headings")
        self.provider_tree.heading("Status", text="Status")
        self.provider_tree.heading("Models", text="Models")
        self.provider_tree.column("Status", width=100)
        self.provider_tree.column("Models", width=80)
        self.provider_tree.pack(fill=tk.BOTH, expand=True)
        
        right_panel = ttk.LabelFrame(main_frame, text="Available Models", padding=10)
        right_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        
        filter_frame = ttk.Frame(right_panel)
        filter_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(filter_frame, text="Provider:").pack(side=tk.LEFT, padx=5)
        self.provider_filter = ttk.Combobox(filter_frame, values=["All"], state="readonly")
        self.provider_filter.pack(side=tk.LEFT, padx=5)
        self.provider_filter.bind("<<ComboboxSelected>>", self._on_provider_filter)
        
        ttk.Button(filter_frame, text="Refresh", command=self._refresh_models).pack(side=tk.RIGHT, padx=5)
        
        self.model_list = tk.Listbox(right_panel, selectmode=tk.SINGLE)
        self.model_list.pack(fill=tk.BOTH, expand=True, pady=5)
        
        bottom_panel = ttk.LabelFrame(main_frame, text="Active Task", padding=10)
        bottom_panel.pack(side=tk.BOTTOM, fill=tk.X, pady=5)
        
        self.task_label = ttk.Label(bottom_panel, text="No active task")
        self.task_label.pack(anchor=tk.W)
        
        self.task_progress = ttk.Progressbar(bottom_panel, mode="indeterminate")
        self.task_progress.pack(fill=tk.X, pady=5)
    
    def _refresh_dashboard(self):
        def fetch():
            health = get_health()
            self.gateway_status = health.get("status", "error")
            stats = get_provider_stats()
            self.providers_data = stats
            models_data = get_all_models()
            if models_data["status"] == "ok":
                self.all_models = models_data
            task = get_active_task()
            self.root.after(0, lambda: self._update_ui(health, stats, models_data, task))
        
        thread = threading.Thread(target=fetch, daemon=True)
        thread.start()
    
    def _update_ui(self, health, stats, models_data, task):
        providers_count = len([p for p, s in stats.items() if s["configured"]])
        models_count = models_data.get("total_models", 0)
        status_text = f"Gateway: {self.gateway_status} | Providers: {providers_count} | Models: {models_count}"
        self.status_bar.config(text=status_text)
        
        self.provider_tree.delete(*self.provider_tree.get_children())
        for provider, data in stats.items():
            status = "✓" if data["configured"] else "✗"
            models_count = data["models"].get("count", 0)
            self.provider_tree.insert("", tk.END, values=(provider, status, models_count))
        
        providers = ["All"] + list(stats.keys())
        self.provider_filter["values"] = providers
        self.provider_filter.current(0)
        
        self._update_model_list("All")
        
        if task:
            self.task_label.config(text=f"Task: {task.get('id', 'N/A')} - {task.get('status', 'N/A')}")
            self.task_progress.start(10)
        else:
            self.task_label.config(text="No active task")
            self.task_progress.stop()
    
    def _update_model_list(self, provider_filter: str):
        self.model_list.delete(0, tk.END)
        
        if provider_filter == "All":
            models_by_provider = self.all_models.get("models_by_provider", {})
            for prov, models in models_by_provider.items():
                for model in models[:50]:
                    self.model_list.insert(tk.END, f"[{prov}] {model}")
        else:
            models = self.providers_data.get(provider_filter, {}).get("models", {}).get("models", [])
            for model in models[:100]:
                self.model_list.insert(tk.END, model)
    
    def _on_provider_filter(self, event):
        provider = self.provider_filter.get()
        self._update_model_list(provider)
    
    def _refresh_models(self):
        from .api import refresh_models
        refresh_models()
        self._refresh_dashboard()
    
    def _show_gateway_settings(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("Gateway Settings")
        dialog.geometry("400x300")
        
        ttk.Label(dialog, text="Gateway URL:").pack(pady=5)
        url_entry = ttk.Entry(dialog, width=50)
        url_entry.insert(0, "http://localhost:8000")
        url_entry.pack(pady=5)
        
        ttk.Button(dialog, text="Save", command=dialog.destroy).pack(pady=10)
        ttk.Button(dialog, text="Test Connection", command=lambda: self._test_connection(url_entry.get())).pack(pady=5)
    
    def _test_connection(self, url: str):
        import httpx
        try:
            response = httpx.get(f"{url}/health", timeout=5.0)
            response.raise_for_status()
            messagebox.showinfo("Success", f"Connected to Gateway!\n{response.json()}")
        except Exception as e:
            messagebox.showerror("Error", f"Cannot connect: {e}")
    
    def _show_about(self):
        messagebox.showinfo(
            "About",
            "Bossman Command Center v3.0\n\n"
            "Gateway Integration:\n"
            "- 9 LLM providers\n"
            "- Auto model discovery\n"
            "- Real-time health monitoring"
        )


def main():
    root = tk.Tk()
    app = DashboardApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
