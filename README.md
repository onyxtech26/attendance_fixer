# MPT Attendance Fixer

A small offline Windows app (works on Windows 7, 32-bit and 64-bit). It checks and fixes the
**Employee Attendance Record** downloaded from the attendance machine, then gets all 3
reports ready to email to HR.

## What it does

1. **Pick the 3 downloaded files.** The app works out which file is which by itself.
2. **Pick the period.** The choices are the 15th, the 25th and the last day of the month. The
   period always starts on the 1st. If the file has days after the date you pick, those days
   are removed from the Employee Attendance Record, and its "Attendance date" is changed to match.
3. **Check attendance.** Every problem is listed with a suggested fix:
   - missing punches (forgot to punch, or the rest of the day on the download day)
   - late in: the suggestion is a few minutes *before* the shift starts, never exactly on time
   - left early: the last punch-out moves to closing time
   - a break longer than 1 h 30 (each break is checked separately)
   - extra or duplicate punches

   Made-up times are based on each person's own habits, such as when they usually go on break,
   with a few random minutes added so they look natural.
   Nothing changes until someone presses **Accept**, **Accept all** or **Edit...**.
   **Other times** gives a new set of natural-looking times. **Add / edit any day...** lets you
   key in any day by hand (for example an empty day, or the rest of today).
4. **Save files for HR.** This makes a folder next to the downloaded files, for example
   `MPT ATTENDANCE 01.10.2026 - 25.10.2026`, holding:
   - `Employee Attendance Record MRT 01.10.26-25.10.26.xls` (fixed, still locked like the original)
   - `Attendance Report MRT 01.10.26-25.10.26.xls` (copied as-is)
   - `Abnormal Attendance Statistic Report MRT 01.10.26-25.10.26.xls` (copied as-is)

   A window then shows the To address (`hr@mptwatches.com`) and the subject, with Copy buttons,
   plus **Open folder** and **Open email program**. Attach the 3 files and press Send.

### Sending again later in the month (15th → 25th → end of month)

Always download fresh files from the machine. They start from the 1st again with the
original punches. The app remembers every day it has sent to HR, in the `memory` folder
next to the app, one file per month. On the next send, those days are put back **exactly as
they were sent before**, and only the new days are checked. For example, the 1–25 file keeps
your 1–15 fixes, including the made-up end of the 15th, even though the machine has the real
punches. Days put back this way show in the list as **Sent** (grey-green). Changing one asks
for confirmation first.

If a send has to be redone, for example the 15th again, pick that date again. The 15th's
memory is replaced. **Forget what was sent this month...** clears the month, but only use it
if those files never went to HR. Keep the `memory` folder with the app if you move it to
another PC.

The downloaded files are never changed. A list of every change is saved in the `logs` folder
next to the app.

## Shifts and rules

| Shift    | Hours         | Breaks |
|----------|---------------|--------|
| Full day | 10:00 – 21:45 | 2      |
| Morning  | 10:00 – 19:00 | 1      |
| Noon     | 12:45 – 21:45 | 1      |

Each break can be at most 90 minutes. The outlet code, HR email, subject text, shift times and
maximum break are all in `settings.json`, which is created next to the app the first time it
runs.

## Getting the .exe

GitHub Actions builds `AttendanceFixer.exe` (32-bit, Python 3.8) on every push. Open the
repository's **Actions** tab, then the latest **Build Windows 7 app** run, and download the
**AttendanceFixer-win7-32bit** artifact. Copy the exe to the shop PC; nothing needs installing.

If Windows 7 says `api-ms-win-crt-runtime-l1-1-0.dll is missing`, install the Microsoft
update *KB2999226* (Universal C Runtime) once.

## Running from source

```
python -m attendance_fixer
python -m unittest discover -s tests
```

It uses only the Python standard library (Tkinter). No Excel and no extra packages are needed.
