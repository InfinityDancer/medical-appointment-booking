from supabase_config import get_supabase_client
from typing import List, Dict, Any, Optional
from datetime import datetime

class SupabaseService:
    """
    Service class for handling Supabase database operations.
    Provides methods for CRUD operations on Supabase tables.
    """
    
    def __init__(self):
        self.client = get_supabase_client()
    
    # ==================== READ OPERATIONS ====================
    
    def get_by_id(self, table: str, id: Any) -> Optional[Dict]:
        """
        Fetch a single record by ID.
        
        Args:
            table: Table name
            id: Record ID
            
        Returns:
            Dictionary with the record or None if not found
        """
        try:
            response = self.client.table(table).select("*").eq("id", id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            print(f"❌ Error fetching from {table}: {e}")
            raise
    
    def get_all(self, table: str, limit: Optional[int] = None) -> List[Dict]:
        """
        Fetch all records from a table.
        
        Args:
            table: Table name
            limit: Optional limit on number of records
            
        Returns:
            List of records
        """
        try:
            query = self.client.table(table).select("*")
            if limit:
                query = query.limit(limit)
            response = query.execute()
            return response.data if response.data else []
        except Exception as e:
            print(f"❌ Error fetching from {table}: {e}")
            raise
    
    def query(self, table: str, filters: Dict[str, Any]) -> List[Dict]:
        """
        Fetch records with filters.
        
        Args:
            table: Table name
            filters: Dictionary of column-value pairs for filtering
            
        Returns:
            List of matching records
        """
        try:
            query = self.client.table(table).select("*")
            for column, value in filters.items():
                query = query.eq(column, value)
            response = query.execute()
            return response.data if response.data else []
        except Exception as e:
            print(f"❌ Error querying {table}: {e}")
            raise
    
    # ==================== CREATE OPERATIONS ====================
    
    def insert(self, table: str, data: Dict) -> Dict:
        """
        Insert a new record.
        
        Args:
            table: Table name
            data: Dictionary with column-value pairs
            
        Returns:
            Inserted record
        """
        try:
            response = self.client.table(table).insert(data).execute()
            if response.data:
                print(f"✅ Record inserted into {table}")
                return response.data[0]
            return {}
        except Exception as e:
            print(f"❌ Error inserting into {table}: {e}")
            raise
    
    def insert_batch(self, table: str, data_list: List[Dict]) -> List[Dict]:
        """
        Insert multiple records at once.
        
        Args:
            table: Table name
            data_list: List of dictionaries with column-value pairs
            
        Returns:
            List of inserted records
        """
        try:
            response = self.client.table(table).insert(data_list).execute()
            print(f"✅ {len(response.data)} records inserted into {table}")
            return response.data if response.data else []
        except Exception as e:
            print(f"❌ Error batch inserting into {table}: {e}")
            raise
    
    # ==================== UPDATE OPERATIONS ====================
    
    def update(self, table: str, id: Any, data: Dict) -> Dict:
        """
        Update a record by ID.
        
        Args:
            table: Table name
            id: Record ID
            data: Dictionary with columns to update
            
        Returns:
            Updated record
        """
        try:
            response = self.client.table(table).update(data).eq("id", id).execute()
            if response.data:
                print(f"✅ Record updated in {table}")
                return response.data[0]
            return {}
        except Exception as e:
            print(f"❌ Error updating {table}: {e}")
            raise
    
    def update_where(self, table: str, filters: Dict[str, Any], data: Dict) -> List[Dict]:
        """
        Update records matching conditions.
        
        Args:
            table: Table name
            filters: Dictionary of conditions for matching records
            data: Dictionary with columns to update
            
        Returns:
            List of updated records
        """
        try:
            query = self.client.table(table).update(data)
            for column, value in filters.items():
                query = query.eq(column, value)
            response = query.execute()
            print(f"✅ Records updated in {table}")
            return response.data if response.data else []
        except Exception as e:
            print(f"❌ Error updating {table}: {e}")
            raise
    
    async def handle_template_status_update(self, value: Dict[str, Any]) -> None:
        """
        Handle Meta webhook template_status events and update Supabase.
        Maps Meta's UPPERCASE status to database enum format.
        
        Args:
            value: Dictionary containing Meta webhook data with keys:
                   - event: Status event (APPROVED, REJECTED, PENDING_DELETION, DISABLED)
                   - message_template_id: Template ID from Meta
                   - message_template_name: Template name from Meta
                   - reason: (Optional) Rejection reason from Meta
        """
        # Map Meta event → database status enum
        status_map = {
            "APPROVED": "Approved",
            "REJECTED": "Rejected",
            "PENDING_DELETION": "Pending",
            "DISABLED": "Rejected",
        }
        
        new_status = status_map.get(value.get("event"))
        
        if not new_status:
            print(f"⚠️  Unknown status event: {value.get('event')}")
            return
        
        template_id = value.get("message_template_id")
        template_name = value.get("message_template_name")
        
        print(f"🔄 Updating template {template_id} ({template_name}) → {new_status}")
        
        try:
            response = self.client.table("whatsapp_template").update({
                "template_status": new_status,
                "rejection_reason": value.get("reason"),  # None if not provided
                "updated_at": datetime.utcnow().isoformat(),
            }).eq("template_id", str(template_id)).execute()
            
            if response.data:
                print(f"✅ Template {template_id} updated to {new_status}")
            else:
                print(f"⚠️  Template {template_id} not found in database")
        except Exception as e:
            print(f"❌ Failed to update template status: {str(e)}")
            raise
    
    # ==================== DELETE OPERATIONS ====================
    
    def delete(self, table: str, id: Any) -> Dict:
        """
        Delete a record by ID.
        
        Args:
            table: Table name
            id: Record ID
            
        Returns:
            Response data
        """
        try:
            response = self.client.table(table).delete().eq("id", id).execute()
            print(f"✅ Record deleted from {table}")
            return response.data if response.data else {}
        except Exception as e:
            print(f"❌ Error deleting from {table}: {e}")
            raise
    
    def delete_where(self, table: str, filters: Dict[str, Any]) -> List[Dict]:
        """
        Delete records matching conditions.
        
        Args:
            table: Table name
            filters: Dictionary of conditions for matching records
            
        Returns:
            List of deleted records
        """
        try:
            query = self.client.table(table).delete()
            for column, value in filters.items():
                query = query.eq(column, value)
            response = query.execute()
            print(f"✅ Records deleted from {table}")
            return response.data if response.data else []
        except Exception as e:
            print(f"❌ Error deleting from {table}: {e}")
            raise
    
    # ==================== HELPER METHODS ====================
    
    def table_exists(self, table: str) -> bool:
        """
        Check if a table exists in the database.
        
        Args:
            table: Table name
            
        Returns:
            True if table exists, False otherwise
        """
        try:
            self.client.table(table).select("*").limit(1).execute()
            return True
        except Exception:
            return False


# Create singleton instance for easy import
supabase_service = SupabaseService()
