// Alflor Studio - ponte nativa DirectShow (IAMCameraControl / IAMVideoProcAmp).
// Compilado automaticamente pela app com o csc.exe do .NET Framework 4 (C# 5).
// Protocolo: uma linha por pedido "<id> <comando> [argumentos]" -> "<id> <json>".
// Funciona em paralelo com outra aplicação a usar a câmara (ex.: OBS).
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
using System.Text;

namespace PtzStudio
{
    [ComImport, Guid("29840822-5B84-11D0-BD3B-00A0C911CE86"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface ICreateDevEnum
    {
        [PreserveSig] int CreateClassEnumerator([In] ref Guid clsid, out IEnumMoniker e, int flags);
    }

    [ComImport, Guid("55272A00-42CB-11CE-8135-00AA004BB851"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IPropertyBag
    {
        [PreserveSig] int Read([MarshalAs(UnmanagedType.LPWStr)] string name, [Out, MarshalAs(UnmanagedType.Struct)] out object val, IntPtr log);
        [PreserveSig] int Write([MarshalAs(UnmanagedType.LPWStr)] string name, [In, MarshalAs(UnmanagedType.Struct)] ref object val);
    }

    [ComImport, Guid("C6E13370-30AC-11d0-A18C-00A0C9118956"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAMCameraControl
    {
        [PreserveSig] int GetRange(int p, out int min, out int max, out int step, out int def, out int flags);
        [PreserveSig] int Set(int p, int v, int flags);
        [PreserveSig] int Get(int p, out int v, out int flags);
    }

    [ComImport, Guid("C6E13360-30AC-11d0-A18C-00A0C9118956"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    interface IAMVideoProcAmp
    {
        [PreserveSig] int GetRange(int p, out int min, out int max, out int step, out int def, out int flags);
        [PreserveSig] int Set(int p, int v, int flags);
        [PreserveSig] int Get(int p, out int v, out int flags);
    }

    static class Program
    {
        static readonly Guid CLSID_SystemDeviceEnum = new Guid("62BE5D10-60EB-11d0-BD3B-00A0C911CE86");
        static readonly Guid VideoCategory = new Guid("860BB310-5D01-11d0-BD3B-00A0C911CE86");
        static readonly Guid AudioCategory = new Guid("33D9A762-90C8-11D0-BD43-00A0C911CE86");
        static readonly Guid IID_IBaseFilter = new Guid("56a86895-0ad4-11ce-b03a-0020af0ba770");

        static readonly string[] CamNames = { "pan", "tilt", "roll", "zoom", "exposure", "iris", "focus" };
        static readonly string[] AmpNames = { "brightness", "contrast", "hue", "saturation", "sharpness", "gamma", "color", "whitebalance", "backlight", "gain" };

        static object filter;
        static IAMCameraControl cam;
        static IAMVideoProcAmp amp;
        static string openedName = "";
        static TextWriter output;

        [STAThread]
        static int Main()
        {
            var input = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false));
            var w = new StreamWriter(Console.OpenStandardOutput(), new UTF8Encoding(false));
            w.AutoFlush = true;
            output = w;
            Reply("0", "{\"ready\":true,\"protocol\":1}");
            string line;
            while ((line = input.ReadLine()) != null)
            {
                line = line.Trim();
                if (line.Length == 0) continue;
                string[] parts = line.Split(new[] { ' ' }, 3);
                string id = parts[0];
                string cmd = parts.Length > 1 ? parts[1] : "";
                string arg = parts.Length > 2 ? parts[2] : "";
                try { Reply(id, Handle(cmd, arg)); }
                catch (Exception ex) { Reply(id, "{\"error\":" + Q(ex.Message) + "}"); }
                if (cmd == "quit") break;
            }
            CloseDevice();
            return 0;
        }

        static void Reply(string id, string json) { output.WriteLine(id + " " + json); }

        static string Handle(string cmd, string arg)
        {
            switch (cmd)
            {
                case "ping": return "{\"ok\":true}";
                case "list": return "{\"video\":" + NameList(VideoCategory) + ",\"audio\":" + NameList(AudioCategory) + "}";
                case "open": return Open(int.Parse(arg, CultureInfo.InvariantCulture));
                case "info": return Info();
                case "cam": return SetProp(true, arg);
                case "amp": return SetProp(false, arg);
                case "close": CloseDevice(); return "{\"ok\":true}";
                case "quit": return "{\"ok\":true}";
                default: return "{\"error\":" + Q("comando desconhecido: " + cmd) + "}";
            }
        }

        static List<IMoniker> Monikers(Guid category)
        {
            var list = new List<IMoniker>();
            Type t = Type.GetTypeFromCLSID(CLSID_SystemDeviceEnum);
            var devEnum = (ICreateDevEnum)Activator.CreateInstance(t);
            IEnumMoniker em;
            Guid cat = category;
            if (devEnum.CreateClassEnumerator(ref cat, out em, 0) != 0 || em == null) return list;
            IMoniker[] m = new IMoniker[1];
            while (em.Next(1, m, IntPtr.Zero) == 0) list.Add(m[0]);
            return list;
        }

        static string FriendlyName(IMoniker m)
        {
            object bag;
            Guid bagId = typeof(IPropertyBag).GUID;
            m.BindToStorage(null, null, ref bagId, out bag);
            object v;
            if (((IPropertyBag)bag).Read("FriendlyName", out v, IntPtr.Zero) != 0) return "";
            return v as string ?? "";
        }

        static string NameList(Guid category)
        {
            var sb = new StringBuilder("[");
            bool first = true;
            foreach (var m in Monikers(category))
            {
                if (!first) sb.Append(',');
                first = false;
                sb.Append(Q(FriendlyName(m)));
            }
            return sb.Append(']').ToString();
        }

        static string Open(int index)
        {
            CloseDevice();
            var list = Monikers(VideoCategory);
            if (index < 0 || index >= list.Count) return "{\"error\":" + Q("índice de dispositivo inválido") + "}";
            object f;
            Guid iid = IID_IBaseFilter;
            list[index].BindToObject(null, null, ref iid, out f);
            filter = f;
            cam = f as IAMCameraControl;
            amp = f as IAMVideoProcAmp;
            openedName = FriendlyName(list[index]);
            return "{\"ok\":true,\"name\":" + Q(openedName) + ",\"cam\":" + Props(true) + ",\"amp\":" + Props(false) + "}";
        }

        static string Info()
        {
            if (filter == null) return "{\"error\":" + Q("nenhum dispositivo aberto") + "}";
            return "{\"name\":" + Q(openedName) + ",\"cam\":" + Props(true) + ",\"amp\":" + Props(false) + "}";
        }

        static string Props(bool isCam)
        {
            var sb = new StringBuilder("{");
            bool first = true;
            string[] names = isCam ? CamNames : AmpNames;
            for (int i = 0; i < names.Length; i++)
            {
                int mn, mx, st, df, fl, v, vf, hr;
                if (isCam)
                {
                    if (cam == null) break;
                    hr = cam.GetRange(i, out mn, out mx, out st, out df, out fl);
                    if (hr != 0) continue;
                    if (cam.Get(i, out v, out vf) != 0) { v = df; vf = 0; }
                }
                else
                {
                    if (amp == null) break;
                    hr = amp.GetRange(i, out mn, out mx, out st, out df, out fl);
                    if (hr != 0) continue;
                    if (amp.Get(i, out v, out vf) != 0) { v = df; vf = 0; }
                }
                if (!first) sb.Append(',');
                first = false;
                sb.AppendFormat(CultureInfo.InvariantCulture,
                    "\"{0}\":{{\"min\":{1},\"max\":{2},\"step\":{3},\"def\":{4},\"caps\":{5},\"cur\":{6},\"flags\":{7}}}",
                    names[i], mn, mx, st, df, fl, v, vf);
            }
            return sb.Append('}').ToString();
        }

        // argumentos: "<nome> <valor> <flags>"  (flags: 1 = automático, 2 = manual)
        static string SetProp(bool isCam, string arg)
        {
            string[] a = arg.Split(' ');
            if (a.Length < 2) return "{\"error\":" + Q("argumentos em falta") + "}";
            string[] names = isCam ? CamNames : AmpNames;
            int idx = Array.IndexOf(names, a[0]);
            if (idx < 0) return "{\"error\":" + Q("propriedade desconhecida: " + a[0]) + "}";
            int value = int.Parse(a[1], CultureInfo.InvariantCulture);
            int flags = a.Length > 2 ? int.Parse(a[2], CultureInfo.InvariantCulture) : 2;
            int hr;
            if (isCam)
            {
                if (cam == null) return "{\"error\":" + Q("sem controlo de câmara") + "}";
                hr = cam.Set(idx, value, flags);
            }
            else
            {
                if (amp == null) return "{\"error\":" + Q("sem controlo de imagem") + "}";
                hr = amp.Set(idx, value, flags);
            }
            return "{\"hr\":" + hr.ToString(CultureInfo.InvariantCulture) + "}";
        }

        static void CloseDevice()
        {
            if (filter != null)
            {
                try { Marshal.ReleaseComObject(filter); } catch { }
            }
            filter = null; cam = null; amp = null; openedName = "";
        }

        static string Q(string s)
        {
            var sb = new StringBuilder("\"");
            foreach (char c in s ?? "")
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20) sb.AppendFormat("\\u{0:x4}", (int)c);
                        else sb.Append(c);
                        break;
                }
            }
            return sb.Append('"').ToString();
        }
    }
}
