using System;
using LibreHardwareMonitor.Hardware;

// One shot, CPU only. No file/pipe paths or other user-controlled arguments.
// Exit 3001..4500 = temperature in tenths C + 3000; 1 = unavailable.
internal sealed class CpuVisitor : IVisitor
{
    public void VisitComputer(IComputer computer) { computer.Traverse(this); }
    public void VisitHardware(IHardware hardware)
    {
        hardware.Update();
        foreach (IHardware sub in hardware.SubHardware) sub.Accept(this);
    }
    public void VisitSensor(ISensor sensor) { }
    public void VisitParameter(IParameter parameter) { }
}

internal static class Program
{
    private static int Main(string[] args)
    {
        if (args.Length != 1 || args[0] != "--once") return 1;
        Computer computer = null;
        try
        {
            computer = new Computer { IsCpuEnabled = true };
            computer.Open();
            computer.Accept(new CpuVisitor());
            float? preferred = null;
            float? core = null;
            foreach (IHardware hw in computer.Hardware)
            {
                if (hw.HardwareType != HardwareType.Cpu) continue;
                foreach (ISensor sensor in hw.Sensors)
                {
                    if (sensor.SensorType != SensorType.Temperature || !sensor.Value.HasValue) continue;
                    float value = sensor.Value.Value;
                    if (float.IsNaN(value) || float.IsInfinity(value) || value <= 0 || value > 150) continue;
                    string name = sensor.Name ?? "";
                    if (!preferred.HasValue &&
                        (name.IndexOf("Tctl", StringComparison.OrdinalIgnoreCase) >= 0 ||
                         name.IndexOf("Package", StringComparison.OrdinalIgnoreCase) >= 0))
                        preferred = value;
                    if (!core.HasValue && name.IndexOf("Core", StringComparison.OrdinalIgnoreCase) >= 0)
                        core = value;
                }
            }
            float? result = preferred ?? core;
            if (!result.HasValue) return 1;
            int tenths = (int)Math.Round(result.Value * 10, MidpointRounding.AwayFromZero);
            return tenths >= 1 && tenths <= 1500 ? 3000 + tenths : 1;
        }
        catch (Exception) { return 1; }
        finally { if (computer != null) try { computer.Close(); } catch (Exception) { } }
    }
}
