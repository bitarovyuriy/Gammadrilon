using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using System.Threading;
using LibreHardwareMonitor.Hardware;

// GammadrilonSensors — мост между приложением и LibreHardwareMonitor.
//
// Зачем отдельный процесс: LHM читает датчики через драйвер PawnIO, а .NET-сборку
// нельзя грузить в питоновский процесс без pythonnet. Хелпер держит LHM открытым и
// печатает по строке JSON на замер, поэтому приложению не нужно запускать процесс
// на каждый тик.
//
// Режимы:
//   --stream [мс]  печатать замер раз в N мс (по умолчанию 1000)
//   --once         один замер и выход
//   --list         полный список сенсоров (диагностика)
internal sealed class UpdateVisitor : IVisitor
{
    public void VisitComputer(IComputer computer)
    {
        computer.Traverse(this);
    }

    public void VisitHardware(IHardware hardware)
    {
        hardware.Update();
        foreach (IHardware sub in hardware.SubHardware)
        {
            sub.Accept(this);
        }
    }

    public void VisitSensor(ISensor sensor)
    {
    }

    public void VisitParameter(IParameter parameter)
    {
    }
}

internal static class Program
{
    private const string NoDriverHint =
        "драйвер PawnIO не установлен";

    private static int Main(string[] args)
    {
        string mode = args.Length > 0 ? args[0] : "--stream";
        int interval = 1000;
        string outPath = null;
        int parentPid = 0;
        for (int i = 1; i < args.Length; i++)
        {
            int parsed;
            if (args[i] == "--out" && i + 1 < args.Length)
            {
                outPath = args[++i];
            }
            else if (args[i] == "--parent" && i + 1 < args.Length
                     && int.TryParse(args[++i], NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed))
            {
                parentPid = parsed;
            }
            else if (int.TryParse(args[i], NumberStyles.Integer, CultureInfo.InvariantCulture, out parsed) && parsed >= 200)
            {
                interval = parsed;
            }
        }

        Computer computer = null;
        string openError = null;
        try
        {
            computer = new Computer
            {
                IsCpuEnabled = true,
                IsMotherboardEnabled = true,
                IsControllerEnabled = false,
                IsGpuEnabled = false,
                IsMemoryEnabled = false,
                IsStorageEnabled = false,
                IsNetworkEnabled = false
            };
            computer.Open();
            computer.Accept(new UpdateVisitor());
        }
        catch (Exception ex)
        {
            openError = ex.GetType().Name + ": " + ex.Message;
        }

        if (mode == "--list")
        {
            string dump = openError == null ? Dump(computer) : Error(openError);
            Console.Out.WriteLine(dump);
            if (outPath != null)
            {
                WriteAtomic(outPath, dump);
            }
            return 0;
        }

        if (mode == "--once")
        {
            string one = openError == null ? Sample(computer) : Error(openError);
            Console.Out.WriteLine(one);
            if (outPath != null)
            {
                WriteAtomic(outPath, one);
            }
            return 0;
        }

        // поток: одна строка на замер, ошибки не роняют процесс
        while (true)
        {
            if (parentPid > 0 && !ParentAlive(parentPid))
            {
                return 0;               // окно закрылось — хелперу тоже пора
            }
            string line;
            try
            {
                if (computer == null)
                {
                    line = Error(openError ?? NoDriverHint);
                }
                else
                {
                    computer.Accept(new UpdateVisitor());
                    line = Sample(computer);
                }
            }
            catch (Exception ex)
            {
                line = Error(ex.GetType().Name + ": " + ex.Message);
            }
            Console.Out.WriteLine(line);
            Console.Out.Flush();
            if (outPath != null)
            {
                WriteAtomic(outPath, line);
            }
            Thread.Sleep(interval);
        }
    }

    //: атомарная запись: читатель никогда не увидит половину строки
    private static void WriteAtomic(string path, string text)
    {
        try
        {
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, text, new UTF8Encoding(false));
            if (File.Exists(path))
            {
                File.Replace(tmp, path, null);
            }
            else
            {
                File.Move(tmp, path);
            }
        }
        catch (Exception)
        {
            // не смогли записать — следующий тик попробует снова
        }
    }

    private static bool ParentAlive(int pid)
    {
        try
        {
            using (System.Diagnostics.Process p = System.Diagnostics.Process.GetProcessById(pid))
            {
                return !p.HasExited;
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static string Sample(Computer computer)
    {
        float? cpu = null;
        float? cpuMax = null;
        float? vrm = null;
        List<string> boards = new List<string>();

        foreach (IHardware hw in computer.Hardware)
        {
            if (hw.HardwareType == HardwareType.Cpu)
            {
                PickCpu(hw.Sensors, ref cpu, ref cpuMax);
            }
            else if (hw.HardwareType == HardwareType.Motherboard)
            {
                PickBoard(hw.Sensors, ref vrm, boards);
                foreach (IHardware sub in hw.SubHardware)
                {
                    PickBoard(sub.Sensors, ref vrm, boards);
                }
            }
        }

        StringBuilder sb = new StringBuilder(160);
        sb.Append("{\"ok\":true,\"cpu\":").Append(Num(cpu))
          .Append(",\"cpu_max\":").Append(Num(cpuMax))
          .Append(",\"vrm\":").Append(Num(vrm))
          .Append(",\"board\":[");
        for (int i = 0; i < boards.Count; i++)
        {
            if (i > 0)
            {
                sb.Append(',');
            }
            sb.Append('"').Append(Esc(boards[i])).Append('"');
        }
        sb.Append("]}");
        return sb.ToString();
    }

    private static void PickCpu(ISensor[] sensors, ref float? cpu, ref float? cpuMax)
    {
        foreach (ISensor s in sensors)
        {
            if (s.SensorType != SensorType.Temperature || !s.Value.HasValue || s.Value.Value <= 0)
            {
                continue;
            }
            string name = s.Name ?? "";
            float v = s.Value.Value;
            if (name.IndexOf("Tctl", StringComparison.OrdinalIgnoreCase) >= 0
                || name.IndexOf("Package", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                if (cpu == null)
                {
                    cpu = v;
                }
            }
            if (name.IndexOf("Max", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                if (cpuMax == null)
                {
                    cpuMax = v;
                }
            }
            if (cpu == null && name.IndexOf("Core", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                cpu = v;
            }
        }
        if (cpu == null)
        {
            return;
        }
        if (cpuMax == null)
        {
            float top = 0;
            foreach (ISensor s in sensors)
            {
                if (s.SensorType == SensorType.Temperature && s.Value.HasValue && s.Value.Value > top)
                {
                    top = s.Value.Value;
                }
            }
            if (top > 0)
            {
                cpuMax = top;
            }
        }
    }

    private static void PickBoard(ISensor[] sensors, ref float? vrm, List<string> boards)
    {
        foreach (ISensor s in sensors)
        {
            if (s.SensorType != SensorType.Temperature || !s.Value.HasValue || s.Value.Value <= 0)
            {
                continue;
            }
            string name = s.Name ?? "";
            boards.Add(name + "=" + s.Value.Value.ToString("0.#", CultureInfo.InvariantCulture));
            if (vrm == null && name.IndexOf("VRM", StringComparison.OrdinalIgnoreCase) >= 0)
            {
                vrm = s.Value.Value;
            }
        }
    }

    private static string Dump(Computer computer)
    {
        StringBuilder sb = new StringBuilder(4096);
        sb.Append("{\"ok\":true,\"hardware\":[");
        bool firstHw = true;
        foreach (IHardware hw in computer.Hardware)
        {
            if (!firstHw)
            {
                sb.Append(',');
            }
            firstHw = false;
            DumpHardware(sb, hw);
        }
        sb.Append("]}");
        return sb.ToString();
    }

    private static void DumpHardware(StringBuilder sb, IHardware hw)
    {
        sb.Append("{\"name\":\"").Append(Esc(hw.Name)).Append("\",\"type\":\"")
          .Append(hw.HardwareType).Append("\",\"sensors\":[");
        bool first = true;
        foreach (ISensor s in hw.Sensors)
        {
            if (!first)
            {
                sb.Append(',');
            }
            first = false;
            sb.Append("{\"name\":\"").Append(Esc(s.Name)).Append("\",\"type\":\"")
              .Append(s.SensorType).Append("\",\"value\":").Append(Num(s.Value)).Append('}');
        }
        sb.Append("],\"sub\":[");
        bool firstSub = true;
        foreach (IHardware sub in hw.SubHardware)
        {
            if (!firstSub)
            {
                sb.Append(',');
            }
            firstSub = false;
            DumpHardware(sb, sub);
        }
        sb.Append("]}");
    }

    private static string Num(float? value)
    {
        return value.HasValue
            ? value.Value.ToString("0.###", CultureInfo.InvariantCulture)
            : "null";
    }

    private static string Error(string message)
    {
        return "{\"ok\":false,\"error\":\"" + Esc(message) + "\"}";
    }

    private static string Esc(string s)
    {
        if (s == null)
        {
            return "";
        }
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", " ").Replace("\n", " ");
    }
}
