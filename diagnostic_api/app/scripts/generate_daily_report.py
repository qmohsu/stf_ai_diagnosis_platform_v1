
import argparse
import sys
from datetime import datetime, date

from app.db.session import SessionLocal
from app.services.reporting import DailyReporter

def main():
    parser = argparse.ArgumentParser(description="Generate Daily Diagnostic Report")
    parser.add_argument("--date", type=str, help="Date in YYYY-MM-DD format (default: today)")
    parser.add_argument("--output", type=str, default="daily_report.json", help="Output file path")
    
    args = parser.parse_args()
    
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        except ValueError:
            print("Error: Invalid date format. Use YYYY-MM-DD")
            sys.exit(1)
    else:
        target_date = date.today()
        
    print(f"Generating report for {target_date}...")
    
    db = SessionLocal()
    try:
        reporter = DailyReporter(db)
        report = reporter.generate_report(target_date)
        
        reporter.save_report_to_disk(report, args.output)
        
        print(f"Report saved to {args.output}")
        print("Summary:")
        print(f"- Total Diagnoses: {report['metrics']['total_diagnoses']}")
        print(f"- Feedback Count: {report['metrics']['feedback_count']}")
        print(f"- Avg Rating: {report['metrics']['average_rating']}")
        
    except Exception as e:
        print(f"Error generating report: {e}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
