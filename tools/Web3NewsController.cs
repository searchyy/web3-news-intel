using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace Web3NewsController
{
    internal static class Program
    {
        [STAThread]
        private static int Main(string[] args)
        {
            if (args.Length >= 2 && args[0] == "--status-file")
            {
                string root = ProjectLocator.FindProjectRoot();
                string result = RuntimeCommand.Run(root, "-Status");
                File.WriteAllText(args[1], result, new UTF8Encoding(false));
                return result.IndexOf("ERROR:", StringComparison.OrdinalIgnoreCase) >= 0 ? 1 : 0;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
            return 0;
        }
    }

    internal static class ProjectLocator
    {
        public static string FindProjectRoot()
        {
            List<string> candidates = new List<string>();
            string envRoot = Environment.GetEnvironmentVariable("WEB3_NEWS_PROJECT_ROOT");
            AddCandidate(candidates, envRoot);

            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string sidecar = Path.Combine(baseDir, "project_root.txt");
            if (File.Exists(sidecar))
            {
                AddCandidate(candidates, File.ReadAllText(sidecar).Trim());
            }

            AddParents(candidates, baseDir);
            AddParents(candidates, Environment.CurrentDirectory);

            foreach (string candidate in candidates)
            {
                if (IsProjectRoot(candidate))
                {
                    return Path.GetFullPath(candidate);
                }
            }

            return null;
        }

        private static void AddParents(List<string> candidates, string start)
        {
            if (String.IsNullOrEmpty(start))
            {
                return;
            }

            DirectoryInfo current = new DirectoryInfo(Path.GetFullPath(start));
            for (int i = 0; i < 8 && current != null; i++)
            {
                AddCandidate(candidates, current.FullName);
                current = current.Parent;
            }
        }

        private static void AddCandidate(List<string> candidates, string path)
        {
            if (String.IsNullOrWhiteSpace(path))
            {
                return;
            }

            try
            {
                string full = Path.GetFullPath(path.Trim());
                foreach (string existing in candidates)
                {
                    if (String.Equals(existing, full, StringComparison.OrdinalIgnoreCase))
                    {
                        return;
                    }
                }
                candidates.Add(full);
            }
            catch
            {
            }
        }

        private static bool IsProjectRoot(string path)
        {
            if (String.IsNullOrWhiteSpace(path))
            {
                return false;
            }

            return File.Exists(Path.Combine(path, "scripts", "local_runtime.ps1"))
                && Directory.Exists(Path.Combine(path, "frontend"))
                && Directory.Exists(Path.Combine(path, "app"));
        }
    }

    internal static class RuntimeCommand
    {
        public static string Run(string projectRoot, string runtimeSwitches)
        {
            if (String.IsNullOrWhiteSpace(projectRoot))
            {
                return "ERROR: Project root was not found. Start the controller from this repository or set WEB3_NEWS_PROJECT_ROOT.";
            }

            string script = Path.Combine(projectRoot, "scripts", "local_runtime.ps1");
            if (!File.Exists(script))
            {
                return "ERROR: Runtime script was not found: " + script;
            }

            ProcessStartInfo info = new ProcessStartInfo();
            info.FileName = "powershell.exe";
            info.Arguments = "-NoLogo -NoProfile -ExecutionPolicy Bypass -File " + Quote(script) + " " + runtimeSwitches;
            info.WorkingDirectory = projectRoot;
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            info.StandardOutputEncoding = Encoding.UTF8;
            info.StandardErrorEncoding = Encoding.UTF8;
            info.EnvironmentVariables["PYTHONUTF8"] = "1";
            info.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";

            StringBuilder output = new StringBuilder();
            try
            {
                using (Process process = Process.Start(info))
                {
                    output.Append(process.StandardOutput.ReadToEnd());
                    string error = process.StandardError.ReadToEnd();
                    process.WaitForExit();
                    if (!String.IsNullOrWhiteSpace(error))
                    {
                        output.AppendLine();
                        output.AppendLine(error.TrimEnd());
                    }
                    if (process.ExitCode != 0)
                    {
                        output.AppendLine();
                        output.AppendLine("ERROR: command exited with code " + process.ExitCode);
                    }
                }
            }
            catch (Exception ex)
            {
                output.AppendLine("ERROR: " + ex.Message);
            }

            return output.ToString().TrimEnd();
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }
    }

    internal sealed class MainForm : Form
    {
        private readonly string projectRoot;
        private readonly TextBox outputBox;
        private readonly Label statusLabel;
        private readonly List<Button> actionButtons;

        public MainForm()
        {
            projectRoot = ProjectLocator.FindProjectRoot();
            actionButtons = new List<Button>();

            Text = "Web3 News Controller";
            MinimumSize = new Size(820, 560);
            Size = new Size(920, 640);
            StartPosition = FormStartPosition.CenterScreen;
            Font = new Font("Segoe UI", 9F);

            TableLayoutPanel layout = new TableLayoutPanel();
            layout.Dock = DockStyle.Fill;
            layout.ColumnCount = 1;
            layout.RowCount = 3;
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 44F));
            layout.RowStyles.Add(new RowStyle(SizeType.Absolute, 54F));
            layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
            layout.Padding = new Padding(12);
            Controls.Add(layout);

            statusLabel = new Label();
            statusLabel.Dock = DockStyle.Fill;
            statusLabel.TextAlign = ContentAlignment.MiddleLeft;
            statusLabel.AutoEllipsis = true;
            statusLabel.Text = projectRoot == null ? "Project root: not found" : "Project root: " + projectRoot;
            layout.Controls.Add(statusLabel, 0, 0);

            FlowLayoutPanel toolbar = new FlowLayoutPanel();
            toolbar.Dock = DockStyle.Fill;
            toolbar.FlowDirection = FlowDirection.LeftToRight;
            toolbar.WrapContents = false;
            layout.Controls.Add(toolbar, 0, 1);

            Button startButton = AddButton(toolbar, "Start", 92);
            startButton.Click += delegate { RunAction("Start", "-Start -OpenBrowser"); };

            Button stopButton = AddButton(toolbar, "Stop", 92);
            stopButton.Click += delegate { RunAction("Stop", "-Stop"); };

            Button statusButton = AddButton(toolbar, "Status", 92);
            statusButton.Click += delegate { RunAction("Status", "-Status"); };

            Button openWebButton = AddButton(toolbar, "Open Web", 104);
            openWebButton.Click += delegate { OpenUrl("http://127.0.0.1:5173/"); };

            Button logsButton = AddButton(toolbar, "Logs", 92);
            logsButton.Click += delegate { OpenFolder(Path.Combine(projectRoot ?? "", ".runtime")); };

            Button folderButton = AddButton(toolbar, "Folder", 92);
            folderButton.Click += delegate { OpenFolder(projectRoot); };

            Button clearButton = AddButton(toolbar, "Clear", 92);
            clearButton.Click += delegate { outputBox.Clear(); };

            outputBox = new TextBox();
            outputBox.Dock = DockStyle.Fill;
            outputBox.Multiline = true;
            outputBox.ScrollBars = ScrollBars.Both;
            outputBox.ReadOnly = true;
            outputBox.WordWrap = false;
            outputBox.Font = new Font("Consolas", 10F);
            layout.Controls.Add(outputBox, 0, 2);

            if (projectRoot == null)
            {
                AppendLine("ERROR: Project root was not found.");
                SetActionsEnabled(false);
                clearButton.Enabled = true;
            }

            Shown += delegate
            {
                if (projectRoot != null)
                {
                    RunAction("Status", "-Status");
                }
            };
        }

        private Button AddButton(FlowLayoutPanel toolbar, string text, int width)
        {
            Button button = new Button();
            button.Text = text;
            button.Width = width;
            button.Height = 34;
            button.Margin = new Padding(0, 8, 8, 8);
            toolbar.Controls.Add(button);
            actionButtons.Add(button);
            return button;
        }

        private void RunAction(string title, string switches)
        {
            SetActionsEnabled(false);
            AppendLine("");
            AppendLine("[" + DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + "] " + title);
            statusLabel.Text = title + " running...";

            Task.Factory.StartNew(delegate
            {
                return RuntimeCommand.Run(projectRoot, switches);
            }).ContinueWith(delegate(Task<string> task)
            {
                BeginInvoke(new Action(delegate
                {
                    if (task.Exception != null)
                    {
                        AppendLine("ERROR: " + task.Exception.GetBaseException().Message);
                    }
                    else
                    {
                        AppendLine(task.Result);
                    }
                    statusLabel.Text = projectRoot == null ? "Project root: not found" : "Project root: " + projectRoot;
                    SetActionsEnabled(true);
                }));
            });
        }

        private void SetActionsEnabled(bool enabled)
        {
            foreach (Button button in actionButtons)
            {
                button.Enabled = enabled;
            }
        }

        private void AppendLine(string text)
        {
            outputBox.AppendText(text + Environment.NewLine);
        }

        private void OpenUrl(string url)
        {
            try
            {
                Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                AppendLine("ERROR: " + ex.Message);
            }
        }

        private void OpenFolder(string path)
        {
            try
            {
                if (String.IsNullOrWhiteSpace(path) || !Directory.Exists(path))
                {
                    AppendLine("ERROR: folder was not found: " + path);
                    return;
                }
                Process.Start(new ProcessStartInfo(path) { UseShellExecute = true });
            }
            catch (Exception ex)
            {
                AppendLine("ERROR: " + ex.Message);
            }
        }
    }
}
